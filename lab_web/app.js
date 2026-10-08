"use strict";

const $ = (id) => document.getElementById(id);
const names = {neutral:"Arena neutralna", left:"Lewy bodziec", right:"Prawy bodziec", obstacle:"Przeszkoda"};
const conditions = {connected:"Podlaczony", disconnected:"Odlaczone wyjscie", silenced:"Wyciszone DNa02", stimulate_left:"Pobudzenie L", stimulate_right:"Pobudzenie R"};
const colors = ["#087f70", "#c36052", "#91651c", "#526fb5"];
const state = {entries:[], detail:null, index:0, playing:false, camera:"both", token:"", selection:0, job:null, handledJob:null, lastTick:0, clock:0};
const video = $("video");
const icons = () => window.lucide.createIcons();
const fmt = (x, digits=2) => Number(x).toFixed(digits);
const duration = () => state.detail.recording.time_s.at(-1);
const dt = () => state.detail.recording.time_s[1];
const bins = () => state.detail.recording.bin_start_s.length;

function message(text) { $("message").textContent=text; $("message").hidden=!text; }
async function api(path, body) {
  const options = body ? {method:"POST", headers:{"Content-Type":"application/json", "X-Lab-Token":state.token}, body:JSON.stringify(body)} : {};
  const response=await fetch(path, options);
  const data=await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function view(name) {
  for (const key of ["recordings","experiment","results","validation"]) $(key+"-view").hidden=key!==name;
  document.querySelectorAll("[data-view]").forEach(b=>b.classList.toggle("selected",b.dataset.view===name));
  if (name!=="recordings") pause();
  else if(state.detail) requestAnimationFrame(update);
  if(name==="validation") refreshValidation();
}
document.querySelectorAll("[data-view]").forEach(b=>b.addEventListener("click",()=>view(b.dataset.view)));
let validationLoading=false;
async function refreshValidation() {
  if(validationLoading) return;
  validationLoading=true;$("refresh-validation").disabled=true;
  try {
    const data=await api("/api/validation"), r=data.summary;
    const labels={not_started:"Brak wynikow etapu 8",running:"Obliczenia w toku",partial:"Seria czesciowa",failed:"Niepowodzenie kontroli",passed:"Kontrole techniczne zaliczone"};
    $("validation-status").textContent=`${labels[data.status]||data.status}${data.planned ? ` / ${data.completed} z ${data.planned} prob` : ""}`;
    $("validation-progress").hidden=Boolean(r)||!data.planned;
    $("validation-progress").max=data.planned||1;$("validation-progress").value=data.completed;
    $("validation-content").hidden=!r;$("download-validation").hidden=!r;
    if(!r) return;
    $("validation-gain").textContent=fmt(r.selection.gain);
    $("validation-checks").textContent=`${Object.values(r.checks).filter(Boolean).length} / ${Object.keys(r.checks).length}`;
    $("validation-wall").textContent=`${fmt(r.performance.median_wall_s_per_simulated_s,1)} s`;
    $("validation-memory").textContent=`${fmt(r.performance.max_sampled_peak_rss_bytes/1024**2,0)} MiB`;
    $("validation-scope").textContent=`Cel inzynierski: +/-${r.selection.target_turn_deg} deg wzgledem proby neutralnej. Koszt sredni: ${fmt(r.outcomes.mean_default_cost)} -> ${fmt(r.outcomes.mean_selected_cost)} deg-eq. Kalibracja biologiczna: niewykonana.`;
    const appendRow=(body,values)=>{const row=document.createElement("tr");for(const value of values){const cell=document.createElement("td");cell.textContent=value;row.append(cell);}body.append(row);};
    const body=$("validation-table").querySelector("tbody");body.replaceChildren();
    for(const row of r.outcomes.held_out) appendRow(body,[row.seed,fmt(row.default_cost),fmt(row.selected_cost),row.selected_turns_relative_neutral_deg.map(x=>fmt(x,1)).join(" / "),`${row.selected_falls} / 3`]);
    const ablations=$("ablation-table").querySelector("tbody");ablations.replaceChildren();
    const names={connected:"Podlaczony",disconnected:"Odlaczone wyjscie",silenced:"Wyciszone DNa02",no_edges:"Bez synaps",no_vision:"Bez modulacji wzrokowej",no_feedback:"Bez DNa02 -> DNa03"};
    for(const [condition,label] of Object.entries(names)){
      const groups=["left","right"].map(side=>r.outcomes.ablations.filter(x=>x.scenario===side && x.condition===(condition==="connected" ? "disconnected" : condition)));
      const means=groups.map(rows=>rows.reduce((sum,x)=>sum+x.heading_deg+(condition==="connected" ? x.connected_minus_ablation_heading_deg : 0),0)/rows.length);
      appendRow(ablations,[label,...means.map(x=>fmt(x,1)),groups[0].length]);
    }
    for(const [id,name] of [["calibration-plot","calibration"],["ablation-plot","ablations"]]) {
      const url=`/validation-plots/${name}?v=${r.fingerprint}`;
      if($(id).getAttribute("src")!==url) $(id).src=url;
    }
    const full=r.full_brain.runs.full;
    $("validation-brain").textContent=`${full.neurons.toLocaleString("pl-PL")} neuronow / ${full.ordered_pairs.toLocaleString("pl-PL")} par / ${r.configuration.full_brain_duration_s*1000} ms modelu / ${fmt(full.run_and_compile_s)} s obliczen Brian2 z kompilacja / ${full.spikes} impulsow.`;
  } catch(error) {
    $("validation-status").textContent="Nie mozna zweryfikowac raportu: "+error.message;
    $("validation-content").hidden=true;$("download-validation").hidden=true;$("validation-progress").hidden=true;
  } finally {validationLoading=false;$("refresh-validation").disabled=false;}
}
$("refresh-validation").addEventListener("click",refreshValidation);
setInterval(()=>{if(!$("validation-view").hidden)refreshValidation();},5000);
function itemLabel(row) { return `${names[row.scenario]}${row.repeat ? " / powtorzenie" : ""}`; }
function renderList() {
  const query=$("search").value.toLowerCase(), scenario=$("scenario-filter").value;
  const list=$("record-list"); list.replaceChildren();
  const rows=state.entries.filter(row=>(!scenario || row.scenario===scenario) && `${itemLabel(row)} ${conditions[row.condition]} ${row.seed}`.toLowerCase().includes(query));
  $("record-count").textContent=`${rows.length} / ${state.entries.length}`;
  for (const row of rows) {
    const b=document.createElement("button"); b.className="record-item";
    b.classList.toggle("selected",row.id===state.detail?.id); b.setAttribute("aria-pressed",String(row.id===state.detail?.id));
    const title=document.createElement("strong");title.textContent=itemLabel(row);
    const sub=document.createElement("small");sub.textContent=`${conditions[row.condition]} / seed ${row.seed}${row.has_video ? " / kamera" : ""}`;
    b.append(title,sub); b.addEventListener("click",()=>selectRecord(row.id)); list.append(b);
  }
  if (!rows.length) { const text=document.createElement("div");text.className="empty-list";text.textContent="Brak zapisow";list.append(text); }
}
$("search").addEventListener("input",renderList);$("scenario-filter").addEventListener("change",renderList);
async function refreshCatalog() {state.entries=await api("/api/catalog");renderList();renderSummary();}
function enablePlayback(enabled) {for(const id of ["play","reset","previous","next","timeline"]) $(id).disabled=!enabled;}
async function selectRecord(id) {
  const version=++state.selection;
  pause();state.detail=null;enablePlayback(false);message("");
  video.removeAttribute("src");video.load();
  $("record-workspace").setAttribute("aria-busy","true");
  $("record-title").textContent="Wczytywanie zapisu";
  try {
    const data=await api("/api/recordings/"+encodeURIComponent(id));
    if (version!==state.selection) return;
    state.detail=data;state.index=0;state.clock=0;
    const trial=data.trial;
    $("record-title").textContent=itemLabel(trial);
    $("record-meta").textContent=`${conditions[trial.condition]} / seed ${trial.seed} / ${fmt(duration())} s / ${data.recording.spike_times_s.length} impulsow`;
    $("record-source").textContent=id.startsWith("run_") ? "ETAP 7 / PROBA EKSPLORACYJNA" : "ETAP 6 / ARCHIWUM";
    $("timeline").max=bins();$("timeline").value=0;
    $("download").href="/api/recordings/"+id;$("download").download=id+".json";
    $("integrity").textContent=`SHA256 ${data.result_sha256.slice(0,16)} / kontrole ${Object.values(data.checks).filter(Boolean).length}/${Object.keys(data.checks).length}`;
    $("no-video").hidden=data.has_video;
    const joint=$("joint");joint.replaceChildren();
    data.recording.joint_rad[0].forEach((_,i)=>{const o=document.createElement("option");o.value=i;o.textContent=`DOF ${String(i+1).padStart(2,"0")}`;joint.append(o);});
    $("neurons").replaceChildren();
    data.recording.neuron_labels.forEach((label,i)=>{
      const row=document.createElement("tr");row.title=data.recording.neuron_ids[i];
      for(const value of [label,"0","--"]){const cell=document.createElement("td");cell.textContent=value;row.append(cell);}
      $("neurons").append(row);
    });
    $("contacts").replaceChildren();
    for (const leg of ["LF","LM","LH","RF","RM","RH"]) {const item=document.createElement("span");item.className="contact";item.textContent=leg;$("contacts").append(item);}
    if(data.has_video){video.src=`/media/${id}.mp4`;video.playbackRate=Number($("speed").value)*10;video.load();}
    enablePlayback(true);renderList();update();
    if(window.matchMedia("(max-width:680px)").matches) $("archive-panel").open=false;
  } catch(error) {
    if(version===state.selection){$("record-title").textContent="Nie udalo sie wczytac zapisu";message(error.message);}
  } finally {if(version===state.selection) $("record-workspace").setAttribute("aria-busy","false");}
}
function pause() {
  state.playing=false;video.pause();$("play").innerHTML='<i data-lucide="play"></i>';
  $("play").title="Odtworz";$("play").setAttribute("aria-label","Odtworz");icons();
}
async function play() {
  if(!state.detail)return;
  if(state.playing){pause();return;}
  if(state.index>=bins()) seek(0);
  try {
    if(state.detail.has_video){video.playbackRate=Number($("speed").value)*10;await video.play();}
    state.playing=true;state.lastTick=performance.now();state.clock=state.index*dt();
    $("play").innerHTML='<i data-lucide="pause"></i>';$("play").title="Pauza odtwarzania";$("play").setAttribute("aria-label","Pauza odtwarzania");icons();
  }catch(error){pause();message("Nie mozna odtworzyc filmu: "+error.message);}
}
function seek(index) {
  if(!state.detail)return;
  pause();state.index=Math.max(0,Math.min(bins(),Number(index)));state.clock=state.index*dt();
  if(state.detail.has_video && video.readyState>=1) video.currentTime=Math.min(state.clock*10,Math.max(0,video.duration-0.001));
  update();
}
$("play").addEventListener("click",play);$("reset").addEventListener("click",()=>seek(0));
$("previous").addEventListener("click",()=>seek(state.index-1));$("next").addEventListener("click",()=>seek(state.index+1));
$("timeline").addEventListener("input",e=>seek(e.target.value));
$("speed").addEventListener("change",()=>{video.playbackRate=Number($("speed").value)*10;state.lastTick=performance.now();});
$("joint").addEventListener("change",()=>state.detail&&update());
video.addEventListener("loadeddata",()=>{if(state.detail){video.currentTime=Math.min(state.index*dt()*10,video.duration-0.001);drawCamera();}});
video.addEventListener("seeked",drawCamera);
video.addEventListener("ended",()=>{if(state.detail){state.index=bins();pause();update();}});
video.addEventListener("error",()=>{if(state.detail?.has_video)message("Film jest niedostepny. Zapis sygnalow pozostaje dostepny.");});
document.querySelectorAll("[data-camera]").forEach(b=>b.addEventListener("click",()=>{
  state.camera=b.dataset.camera;document.querySelectorAll("[data-camera]").forEach(x=>x.classList.toggle("selected",x===b));
  document.querySelector(".camera-stage").classList.toggle("single",state.camera!=="both");drawCamera();
}));
function tick(now) {
  if(state.playing && state.detail){
    if(state.detail.has_video) state.clock=video.currentTime/10;
    else state.clock+=Math.min((now-state.lastTick)/1000,0.1)*Number($("speed").value);
    state.lastTick=now;state.index=Math.min(bins(),Math.floor((state.clock+1e-8)/dt()));update();
    if(state.index>=bins())pause();
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);

function ctx(id) {
  const c=$(id),w=c.clientWidth,h=c.clientHeight,ratio=window.devicePixelRatio||1;
  c.width=Math.round(w*ratio);c.height=Math.round(h*ratio);
  const x=c.getContext("2d");x.setTransform(ratio,0,0,ratio,0,0);x.font="11px Segoe UI";
  return [x,w,h];
}
window.addEventListener("resize",()=>state.detail&&update());
function drawCamera(){
  const c=$("camera"),both=state.camera==="both";c.width=both?1280:640;c.height=480;
  const x=c.getContext("2d");x.fillStyle="#edf0ef";x.fillRect(0,0,c.width,c.height);
  if(state.detail?.has_video && video.readyState>=2){x.drawImage(video,state.camera==="top"?640:0,64,both?1280:640,480,0,0,c.width,480);}
}
function cursor(x,w,h,left=55) {x.strokeStyle="#343c39";x.lineWidth=1;x.setLineDash([3,3]);const at=left+(w-left-12)*(state.index*dt()/duration());x.beginPath();x.moveTo(at,15);x.lineTo(at,h-25);x.stroke();x.setLineDash([]);}
function drawTrajectory(){
  const [x,w,h]=ctx("trajectory"),d=state.detail.recording,p=d.position_mm;
  const obstacle=state.detail.trial.scenario==="obstacle" ? state.detail.configuration.obstacle : null;
  const xs=p.map(q=>q[0]),ys=p.map(q=>q[1]);
  let xmin=Math.min(...xs)-1,xmax=Math.max(...xs,7.5)+1,ymin=Math.min(...ys,-3)-.5,ymax=Math.max(...ys,3)+.5;
  const scale=Math.min((w-55)/(xmax-xmin),(h-40)/(ymax-ymin));
  const px=v=>35+(v-xmin)*scale,py=v=>h-25-(v-ymin)*scale;
  x.strokeStyle="#e5eae7";x.fillStyle="#6b7771";x.font="11px Segoe UI";
  for(let v=Math.ceil(xmin);v<=xmax;v+=2){x.beginPath();x.moveTo(px(v),12);x.lineTo(px(v),h-25);x.stroke();x.fillText(v,px(v),h-7);}
  for(let v=Math.ceil(ymin);v<=ymax;v+=2){x.beginPath();x.moveTo(35,py(v));x.lineTo(w-10,py(v));x.stroke();x.fillText(v,5,py(v)+4);}
  if(obstacle){const c=obstacle.center_mm,b=obstacle.half_size_mm;x.fillStyle="#86c6c1";x.fillRect(px(c[0]-b[0]),py(c[1]+b[1]),2*b[0]*scale,2*b[1]*scale);}
  x.strokeStyle="#b38a46";x.setLineDash([4,4]);x.beginPath();x.moveTo(px(6.5),py(-3));x.lineTo(px(6.5),py(3));x.stroke();x.setLineDash([]);
  function path(points,color){x.strokeStyle=color;x.lineWidth=2;x.beginPath();points.forEach((q,i)=>i?x.lineTo(px(q[0]),py(q[1])):x.moveTo(px(q[0]),py(q[1])));x.stroke();}
  path(p,"#cfd8d4");path(p.slice(0,state.index+1),colors[0]);
  const current=p[state.index];x.fillStyle=colors[0];x.beginPath();x.arc(px(current[0]),py(current[1]),5,0,Math.PI*2);x.fill();
}
function drawSpikes(){
  const [x,w,h]=ctx("spikes"),d=state.detail.recording,left=85;
  d.neuron_labels.forEach((label,i)=>{const y=28+i*33;x.fillStyle="#49564f";x.fillText(label,1,y+4);x.strokeStyle="#e7ece9";x.beginPath();x.moveTo(left,y);x.lineTo(w-12,y);x.stroke();});
  d.spike_times_s.forEach((t,i)=>{const neuron=d.spike_indices[i],at=left+(w-left-12)*t/duration(),y=28+neuron*33;x.strokeStyle=colors[neuron];x.lineWidth=2;x.beginPath();x.moveTo(at,y-9);x.lineTo(at,y+9);x.stroke();});
  x.fillStyle="#6b7771";for(let i=0;i<=4;i++)x.fillText(fmt(duration()*i/4),left+(w-left-28)*i/4,h-5);
  cursor(x,w,h,left);
}
function drawLines(id,values,max,label,labels){
  const [x,w,h]=ctx(id),left=55,top=20,bottom=h-28;
  x.fillStyle="#69736f";x.fillText(label,1,12);x.font="11px Segoe UI";
  for(let i=0;i<=2;i++){const y=bottom-(bottom-top)*i/2;x.strokeStyle="#e5eae7";x.beginPath();x.moveTo(left,y);x.lineTo(w-12,y);x.stroke();x.fillText(fmt(max*i/2,max===1?1:0),4,y+4);}
  labels.forEach((name,j)=>{
    x.strokeStyle=colors[j];x.lineWidth=2;x.beginPath();
    values.forEach((row,i)=>{const at=left+(w-left-12)*i/values.length,y=bottom-row[j]/max*(bottom-top);if(i)x.lineTo(at,bottom-values[i-1][j]/max*(bottom-top));x.lineTo(at,y);});
    x.lineTo(w-12,bottom-values.at(-1)[j]/max*(bottom-top));x.stroke();x.fillStyle=colors[j];x.font="10px Segoe UI";x.fillText(name,left+j*(w-left-12)/labels.length,12);
  });
  x.fillStyle="#69736f";for(let i=0;i<=4;i++)x.fillText(fmt(duration()*i/4),left+(w-left-24)*i/4,h-5);cursor(x,w,h,left);
}
function drawRetina(){
  const [x,w]=ctx("retina"),values=state.detail.recording.retina[state.index];
  values.forEach((eye,j)=>{x.fillStyle="#5e6c65";x.fillText(j?"R":"L",2,26+j*35);eye.forEach((v,i)=>{const q=Math.max(0,Math.min(255,Math.round(v*255)));x.fillStyle=`rgb(${q},${q},${q})`;x.fillRect(25+i*(w-30)/721,8+j*35,Math.ceil((w-30)/721),27);});});
}
function update(){
  if(!state.detail || $("recordings-view").hidden)return;
  const d=state.detail.recording,i=state.index,t=d.time_s[i],bin=Math.min(i,bins()-1),ended=i-1;
  $("timeline").value=i;$("time-label").textContent=`${fmt(t)} / ${fmt(duration())} s`;
  $("position-x").textContent=fmt(d.position_mm[i][0]);$("position-y").textContent=fmt(d.position_mm[i][1]);$("heading").textContent=fmt(d.heading_deg[i],1);
  $("stimulus-state").textContent=`Bodziec: ${d.panels[i][0]?"lewy":d.panels[i][1]?"prawy":"brak"}`;
  $("camera-time").textContent=state.detail.has_video?`Kamera t = ${fmt(Math.min(i,bins()-1)*dt())} s${i===bins()?" / ostatnia klatka":""}`:"MuJoCo / zapis qpos";
  $("sensor-time").textContent=`t = ${fmt(t)} s`;
  const counts=Array(4).fill(0);d.spike_times_s.forEach((v,k)=>{if(v<t)counts[d.spike_indices[k]]++;});
  [...$("neurons").children].forEach((row,k)=>{row.children[1].textContent=counts[k];row.children[2].textContent=ended>=0?fmt(d.voltage_mv[ended][k],1):"--";});
  $("voltage-time").textContent=ended>=0?`Napiecie: koniec przedzialu t = ${fmt(d.bin_end_s[ended])} s / model LIF` : "Napiecie: brak zakonczonego przedzialu";
  $("command-l").textContent=fmt(d.applied_command[bin][0],3);$("command-r").textContent=fmt(d.applied_command[bin][1],3);
  $("command-time").textContent=i===bins()?"Ostatni zakonczony przedzial":`[${fmt(d.bin_start_s[bin])}, ${fmt(d.bin_end_s[bin])}) s`;
  [...$("contacts").children].forEach((el,k)=>{el.classList.toggle("active",d.contacts[i][k]);el.title=d.contacts[i][k]?"Kontakt":"Bez kontaktu";});
  const joint=Number($("joint").value);$("joint-angle").textContent=fmt(d.joint_rad[i][joint],3)+" rad";$("joint-speed").textContent=fmt(d.joint_velocity_rad_s[i][joint],2)+" rad/s";
  drawCamera();drawTrajectory();drawSpikes();drawLines("inputs",d.input_rates_hz,Math.max(200,...d.input_rates_hz.flat()),"Hz",d.input_labels);
  drawLines("commands",d.applied_command,1,"CPG",["L","R"]);drawRetina();
}
function renderSummary(){
  const body=$("summary-table").querySelector("tbody");body.replaceChildren();
  for(const scenario of Object.keys(names))for(const condition of Object.keys(conditions)){
    const rows=state.entries.filter(r=>r.id.startsWith("archive_")&&!r.repeat&&r.scenario===scenario&&r.condition===condition);
    if(!rows.length)continue;
    const tr=document.createElement("tr"),n=rows.length;
    const values=[names[scenario],conditions[condition],n,fmt(rows.reduce((a,r)=>a+r.metrics.heading_change_deg,0)/n),`${rows.filter(r=>r.metrics.upright_gate_success).length}/${n}`,rows.filter(r=>r.metrics.fell).length];
    values.forEach(v=>{const td=document.createElement("td");td.textContent=v;tr.append(td);});body.append(tr);
  }
}
function updateForm(){
  const stimulation=$("experiment-condition").value.startsWith("stimulate");
  if(stimulation)$("experiment-scenario").value="neutral";
  $("experiment-scenario").disabled=stimulation;$("stimulation").disabled=!stimulation;
}
$("experiment-condition").addEventListener("change",updateForm);
$("experiment-form").addEventListener("submit",async e=>{
  e.preventDefault();$("launch").disabled=true;message("");
  try {const parameters={scenario:$("experiment-scenario").value,condition:$("experiment-condition").value,seed:Number($("seed").value),duration_s:Number($("duration").value),stimulation_hz:Number($("stimulation").value)};
    showJob(await api("/api/jobs",{kind:"simulate",parameters}));
  }catch(error){message(error.message);$("launch").disabled=false;}
});
$("render-video").addEventListener("click",async()=>{
  if(!state.detail)return;$("render-video").disabled=true;
  try{showJob(await api("/api/jobs",{kind:"render",recording_id:state.detail.id}));}catch(error){message(error.message);$("render-video").disabled=false;}
});
$("pause-job").addEventListener("click",async()=>{try{await api("/api/control",{action:state.job?.state==="paused"?"resume":"pause"});}catch(error){message(error.message);}});
$("cancel-job").addEventListener("click",async()=>{try{await api("/api/control",{action:"cancel"});}catch(error){message(error.message);}});
$("open-job").addEventListener("click",()=>{if(state.job?.recording_id){view("recordings");selectRecord(state.job.recording_id);}});
$("show-job").addEventListener("click",()=>view("experiment"));
const statuses={idle:"Gotowy",preparing:"Przygotowanie modelu",running:"Obliczenia",paused:"Wstrzymano",encoding:"Zapis filmu",completed:"Zakonczono",cancelled:"Przerwano",failed:"Blad",interrupted:"Proces przerwany"};
function showJob(job){
  state.job=job;
  const active=["preparing","running","paused","encoding"].includes(job.state),percent=Math.round(job.progress*100);
  $("job-badge").textContent=statuses[job.state]||job.state;$("job-progress").value=job.progress;$("job-percent").textContent=percent+"%";
  $("job-detail").textContent=job.error||statuses[job.state];$("launch").disabled=active;$("render-video").disabled=active;
  $("pause-job").disabled=!active||job.state==="encoding";$("cancel-job").disabled=!active;
  $("pause-job").innerHTML=job.state==="paused"?'<i data-lucide="play"></i>Wznow':'<i data-lucide="pause"></i>Pauza';
  $("open-job").hidden=job.state!=="completed";$("job-strip").hidden=!active;
  $("job-strip-label").textContent=`${statuses[job.state]} / ${percent}%`;icons();
}
async function poll(){
  try{
    const job=await api("/api/job");showJob(job);
    if(job.state==="completed"&&state.handledJob!==job.id){
      state.handledJob=job.id;await refreshCatalog();
      if(state.detail?.id===job.recording_id)await selectRecord(job.recording_id);
    }
  }catch(error){$("job-badge").textContent="Brak polaczenia";$("launch").disabled=true;}
  setTimeout(poll,700);
}
async function start(){
  icons();updateForm();
  try{state.token=(await api("/api/bootstrap")).token;await refreshCatalog();
    const first=state.entries.find(r=>r.id==="archive_obstacle-connected-seed42")||state.entries[0];
    if(first)await selectRecord(first.id);else message("Brak zakonczonych prob");
    poll();
  }catch(error){message(error.message);}
}
start();
