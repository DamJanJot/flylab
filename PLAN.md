# FlyLab: muszka, mozg i srodowisko eksperymentow

Cel: lokalne laboratorium z biomechaniczna muszka, modelem neuronow opartym na
FlyWire i petla srodowisko -> zmysly -> mozg -> sterowanie ruchem -> srodowisko.
Pierwszy zakres to chodzenie po arenie. Lot jest osobnym rozszerzeniem po etapie 8.
Nie zakladamy, ze odtworzenie polaczen samo odtworzy zachowanie zwierzecia.

Plan ma **8 etapow**. Stan poczatkowy: dziala demonstracyjny podglad 28 obwodow;
nie ma jeszcze biomechaniki, pomiaru aktywnosci biologicznej ani petli sensorycznej.

| Etap | Rezultat | Warunek zakonczenia | Stan |
| --- | --- | --- | --- |
| 1. Fundament eksperymentow | Audyt danych, konfiguracja czasow i seedow, eksport grafu z pochodzeniem | Audyt zapisany, poprawny pakiet wejsciowy, testy walidacji | Zakonczony 2026-10-07 |
| 2. Cialo i arena | NeuroMechFly/FlyGym + MuJoCo, podloze, kamera, referencyjny kontroler chodu | Widoczna muszka; kontakt z podlozem; stabilny reset; zapis rzeczywistej trajektorii i wersji zaleznosci | Zakonczony 2026-10-07 |
| 3. Model neuronowy | Import rzeczywistych polaczen, Brian2/LIF, opoznienia, progi i parametry jawne | Testy znanych malych sieci, pobudzenie i wyciszenie; benchmark podgrafu, potem skali pelnego mozgu | Zakonczony technicznie 2026-10-07; kalibracja biologiczna pozostaje |
| 4. Zmysly | Obserwacje wzrokowe, kontaktowe, propriocepcja; pozniej wech | Bodziec w arenie zmienia odpowiedni sygnal sensoryczny; kodowanie do neuronow jest opisane i testowalne | Zakonczony technicznie 2026-10-08; kodowanie zastepcze, mapowanie do FlyWire otwarte |
| 5. Polaczenie mozg-cialo | Adapter sygnalow zstepujacych i kontroler obwodow ruchowych | Zamknieta petla; interwencja w modelu mozgu zmienia komende i ruch; kontrola z odlaczonym mozgiem | Zakonczony technicznie 2026-10-08; 4-komorkowy obwod sterowania, zastepczy wzrok i VNC |
| 6. Testy zachowania | Arena z przeszkodami, kierunkowe bodzce, pobudzenie/wyciszenie neuronow | Powtarzalne serie wielu seedow; porownanie baseline z interwencja i kontrolami | Zakonczony technicznie 2026-10-08; 44 proby, 3 seedy; przejscie przeszkody pozostaje ograniczeniem modelu |
| 7. Panel laboratoryjny | Cialo, sygnaly sensoryczne, aktywnosc neuronow i os czasu obok siebie | Start/pauza/reset, konfiguracja testu, odtwarzanie zapisu; zgodne zegary i jednostki | Zakonczony technicznie 2026-10-08; lokalny panel, odtwarzanie i nowe proby offline |
| 8. Walidacja i wydajnosc | Benchmark, kalibracja, testy ablacji, dokumentacja ograniczen | Odtwarzalny pakiet eksperymentu; zmierzona szybkosc i pamiec; rozdzielenie wplywu mozgu od kontrolera ruchu | Zakonczony technicznie 2026-10-09; 68 prob, kalibracja inzynierska, ablacje i benchmark; biologiczna walidacja pozostaje otwarta |

## Decyzje techniczne

- Cialo: oficjalny NeuroMechFly/FlyGym z MuJoCo. W pierwszej kolejnosci backend CPU.
  Zaleznosci w osobnym srodowisku projektu; przypiete wersje po tescie zgodnosci.
  FlyGym 2.x ma nowe API, niezgodne z 1.x. Nie mieszamy tutoriali tych wersji.
- Mozg: Brian2, z opublikowanym modelem Shiu jako punktem odniesienia.
  Domyslne dane jego repozytorium dotycza v630; przejscie na v783 wymaga jawnej konfiguracji.
  Obecny dodatni model progowy w HTML sluzy do eksploracji; nie jest silnikiem docelowym.
- FlyWire opisuje mozg. Sterowanie nogami wymaga dodatkowego modelu obwodow ruchowych
  (VNC) lub jawnie oznaczonego kontrolera zastepczego, np. CPG. Nie przypisujemy
  dowolnych neuronow do silnikow tylko dlatego, ze maja duzo synaps.
- Kazde mapowanie sensoryczne i ruchowe ma zrodlo, identyfikatory neuronow,
  transformacje jednostek i status: udokumentowane / hipoteza / kontroler zastepczy.
- Pierwsze testy ciala uzywaja kontrolera referencyjnego. Dopiero etap 5 laczy go
  z mozgiem. Wlasciwa walidacja obejmuje test odlaczenia mozgu.
- Zaczynamy od malych sieci. Pelny connectom uruchamiamy dopiero po pomiarze pamieci
  i czasu. Nie obiecujemy pracy w czasie rzeczywistym przed benchmarkiem.
- Warstwa eksperymentow stosuje sekundy i jednostki SI; adapter modelu ciala musi
  sprawdzic jednostki modelu MuJoCo i wykonac konwersje. Zegary maja calkowite proporcje.
- Wyniki przechowuja seed, konfiguracje, wersje kodu/pakietow, hash danych,
  aktywnosc, obserwacje, komendy i trajektorie. Odtwarzanie pomiaru jest oddzielone od symulacji.

## Pierwszy eksperyment

`experiments/baseline.json` definiuje krotki test techniczny z seedem 42.
Na etapie 1 powstaje jedynie pakiet wejsciowy: konfiguracja i 13-wezlowy wycinek
rzeczywistych polaczen. Nie ma jeszcze przypisania tych neuronow do zmyslow ani ruchu.
Paczka ma status `prepared_not_simulated`, aby nie mylic jej z wynikiem eksperymentu.
Zmiana tego wycinka na obwod o uzasadnionej funkcji nastapi w etapach 3-5.

```powershell
python flylab.py doctor
python flylab.py prepare experiments/baseline.json
python -m unittest discover -s tests -v
```

Audyt sprawdza naglowki i probki CSV/GZIP, a nie integralnosc calego wielogigabajtowego
zbioru. Pelne hashowanie i walidacja importu sa kryterium etapu 3. Metadane pakietow
nie dowodza, ze ich import, renderer OpenGL lub modele ciala dzialaja.

## Zrodla sprawdzone 2026-10-07

- NeuroMechFly: https://neuromechfly.org/
- Instalacja i zaleznosci: https://neuromechfly.org/installation/
- Oficjalny kod FlyGym: https://github.com/NeLy-EPFL/flygym
- Model mozgu Shiu / Brian2: https://github.com/philshiu/Drosophila_brain_model
- Publikacja modelu: https://pmc.ncbi.nlm.nih.gov/articles/PMC11446845/
- Dane FlyWire v783: https://zenodo.org/records/10676866

Osiem etapow technicznych zakonczono. Dalsza bramka naukowa to niezalezne
dane biologiczne i kalibracja receptorow, komorek oraz obwodow ruchowych.
RAM zmierzony przez psutil: 34 183 331 840 B (okolo 31.8 GiB). GPU nie byl wymagany;
benchmark etapu 3 korzysta z CPU. Wczesniejszy odczyt RAM/GPU przez CIM nie powiodl sie.

## Wynik etapu 1

- `flylab.py`: dzialajace polecenia `doctor` i `prepare`, walidacja konfiguracji,
  zegarow i interwencji, zachowanie ID jako stringow, hashe grafu i konfiguracji.
- `outputs/flylab/audit.json`: cztery tabele przeszly kontrole naglowkow i pierwszych
  16 wierszy. Python 3.13.7, 12 logicznych CPU, okolo 167 GiB wolnego miejsca.
  RAM/GPU niezmierzone. FlyGym, MuJoCo, Brian2 i NumPy nieobecne w aktywnym interpreterze;
  nie jest to twierdzenie o wszystkich srodowiskach zainstalowanych na komputerze.
- `outputs/flylab/prepared-baseline.json`: 13 neuronow, 33 krawedzie, seed 42,
  klucz eksperymentu `6526f19ff7c1a973`. Pakiet wejsciowy, bez wynikow symulacji.
- `tests/test_flylab.py`: 5 testow przeszlo, w tym powtarzalnosc, duze ID,
  odrzucanie blednych zegarow, interwencji i uszkodzonych tabel.
- W chwili zakonczenia etapu 1 etapy 2-8 pozostawaly do wykonania.

## Wynik etapu 2

- Izolowane srodowisko `.venv-body`: Python 3.13.7, FlyGym 2.1.0, MuJoCo 3.9.0.
  `requirements-body.lock.txt` zawiera pelne przypiete wersje. `pip check` bez konfliktow.
- `body_smoke.py` uruchamia oficjalny kontroler CPG z `flygym_demo.complex_terrain`,
  biomechanike NeuroMechFly, plaska arene i sledzaca kamere. Mozg nie jest podlaczony.
- Dwa przebiegi po 1 s, seed 42, krok fizyki 0.0001 s, 42 sterowane stopnie swobody.
  Rozgrzewka 0.05 s przed kazdym przebiegiem. Przemieszczenie poziome 13.609 mm.
- Zarejestrowano kontakt przynajmniej jednej nogi z podlozem w kazdej probce.
  Os pionowa tulowia pozostala skierowana do gory (minimum z = 0.9883).
- Reset odtworzyl pelny zapis qpos i kontakty identycznie w tym samym srodowisku.
  Nie oznacza to gwarancji identycznosci miedzy roznymi systemami i wersjami silnika.
- Zapisano 100 klatek 640x480; podglad PNG sprawdzony wizualnie, obraz niepusty i ruchomy.
  Film 25 fps odtwarza sekunde symulacji w 4 s. Obliczenie przebiegu z zapisem obrazu
  zajelo okolo 5.3 s, wiec ten skrypt diagnostyczny nie dziala jeszcze w czasie rzeczywistym.
- Osiem kontroli integracyjnych oraz 5 testow etapu 1 przeszlo.

Pliki w `outputs/flylab/body-baseline/`:

- `walking.mp4`, `fly.png`: zapis renderera MuJoCo.
- `trajectory.csv`: czas od konca rozgrzewki, pozycja w metrach, liczba nog w kontakcie,
  skladowa z lokalnej osi pionowej tulowia.
- `state.npz`: qpos, pozycja, kontakty, cele stawow w radianach i stany adhezji.
  qpos zachowuje natywne jednostki MuJoCo (translacja mm, katy rad); nie jest w calosci w SI.
- `settings.json`, `report.json`: parametry, kontrole, wersje i hash skryptu.

Ponowne uruchomienie nadpisuje ten katalog wynikowy. Inny katalog mozna podac przez `--output`:

```powershell
.\.venv-body\Scripts\python.exe body_smoke.py --duration 1 --seed 42
```

Odtworzenie srodowiska w nowym katalogu projektu:

```powershell
python -m venv .venv-body
.\.venv-body\Scripts\python.exe -m pip install -r requirements-body.lock.txt
```

Test ciala ma wlasny `settings.json`; nie korzysta jeszcze z grafu ani interwencji
`experiments/baseline.json`. Polaczenie eksperymentow ciala i mozgu nastapi w etapie 5.
Zrodlo kontrolera: https://neuromechfly.org/tutorials/4a_cpg_controller/

## Wynik etapu 3

- `flywire_data.py` odczytal do konca cztery lokalne pliki GZIP, zweryfikowal strukture
  kazdego wiersza i wymagane pola, zachowal dokladne ID oraz zapisal SHA256 plikow.
  Nie zweryfikowano wszystkich biologicznych adnotacji ani zgodnosci hashy z repozytorium
  zrodlowym. Nie zmieniono i nie pobierano ponownie danych w Downloads/mind.
- Zbior: 139 255 neuronow, 5 342 446 wierszy polaczen, 3 732 460 unikalnych
  skierowanych par po agregacji neuropili, 50 666 648 kontaktow synaptycznych.
  To cala lokalna tabela `connections_princeton.csv.gz`, nie tabela `no_threshold`
  ani pelna reprezentacja wszystkich mechanizmow mozgu.
- `brain_model.py`: Brian2 2.10.1, LIF, stale czasowe, prog, reset, okres refrakcji,
  opoznienia, pobudzanie Poissona i czasowe wyciszanie. Backend NumPy/CPU.
  Parametry, konfiguracja, wersje i hashe kodu sa zapisane z kazdym eksperymentem.
- Znak wagi to hipoteza: ACH +1, GABA/GLUT -1. Pozostale typy i brak adnotacji:
  waga 0, bez modelowania neuromodulacji. W pelnej sieci 311 234 par ma wage 0.
  Szczegoly i roznice wzgledem modelu Shiu opisuje `BRAIN_MODEL.md`.
- Malutki graf z poprzedniego podgladu (13 neuronow / 33 pary) porownano z pelnym
  importem: ID, NT, kierunki i liczby synaps sa identyczne.
- W podgrafie: brak bodzca = 0 impulsow, pobudzenie CT1 = 13 impulsow,
  ten sam bodziec + wyciszenie CT1 = 0 impulsow. Bodziec: 150 Hz w 25-175 ms,
  seed 42, czas proby 250 ms. Powtorzenie wszystkich zapisanych tablic identyczne.
- Pelna siec: 50 ms, krok 0.1 ms, pobudzenie trzech neuronow ACH wybranych po
  liczbie kontaktow wyjsciowych (test techniczny, nie zmysl). Wynik: 725 impulsow,
  473 aktywne neurony; powtorzenie identyczne, kontrola bez bodzca = 0 impulsow.
- Benchmark pierwszego przebiegu pelnej sieci: okolo 3.3 s obliczen z przygotowaniem
  kodu Brian2 + okolo 0.2 s budowania obiektow; import i eksport poza tym pomiarem.
  Oznacza to okolo 66 s czasu sciennego na 1 s symulacji w tym krotkim tescie,
  nie prognoze wydajnosci dlugich przebiegow. Brak pracy w czasie rzeczywistym.
- Szczyt working set calego procesu podczas zestawu prob wyniosl okolo 0.53 GiB.
  Raport rozroznia probkowany RSS od maksimum procesu; to nie jest pamiec samego mozgu.
- 15 testow jednostkowych/integracyjnych oraz 11 kontroli zestawu eksperymentow przeszlo.
  `pip check` bez konfliktow. Test ciala po instalacji Brian2 przeszedl ponownie wszystkie
  osiem kontroli, z tym samym przemieszczeniem i identycznym resetem.
- `comparison.png` i `full-activity.png` zawieraja wykresy faktycznie zapisanych wynikow
  Brian2; oba sprawdzone wizualnie. Nie zastapiono starych demonstracyjnych widokow HTML.

Wazne: skonczylismy etap silnika i jego testow technicznych, nie walidacje zywego mozgu.
Przy tych wagach napiecia moga byc niefizjologiczne (w podgrafie okolo -173 mV).
Model nie uwzglednia potencjalow odwrocenia, receptorow ani wielu efektow komorkowych.
Nie nalezy interpretowac tych wartosci jako przewidywan pomiarow biologicznych.
Mozg i cialo nadal dzialaja osobno. Zamknieta petla pozostaje etapem 5.

Uruchomienie zestawu etapu 3 (nadpisuje swoj katalog wynikow):

```powershell
.\.venv-body\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-body\Scripts\python.exe brain_experiment.py suite
```

Wyniki: `outputs/flylab/brain-stage3/report.json`, wykresy oraz podkatalogi kazdego
przebiegu z `activity.npz`, `config.json`, `parameters.json`, `report.json`.
Import: `outputs/flylab/connectome-cache/manifest.json` i `connectome.npz`.
Odtworzenie wspolnego srodowiska: `requirements-simulation.lock.txt`.
Instrukcje wlasnych eksperymentow i format danych: `BRAIN_MODEL.md`.

## Wynik etapu 4

- `sensory_experiment.py`: dzialajaca arena MuJoCo z dwoma niezderzalnymi panelami
  wizualnymi, natywnymi kamerami oczu FlyGym oraz odczytami ciala.
- Dwa obrazy 450x512, przeksztalcenie fisheye, 721 ommatidiow na oko i dwa rzadkie
  kanaly yellow/pale. Uzywamy kalibracji i plikow dostarczonych z FlyGym 2.1.0;
  ich SHA256 sa zapisane w raporcie.
- Rejestrujemy kontakt kazdej z szesciu nog, 66 katow i predkosci katowych stawow,
  polozenie tulowia oraz qpos. Do kodowania propriocepcji trafiaja 42 aktywne DOF.
  Kontakt jest binarnym odczytem symulatora, nie modelem biologicznego receptora.
- Przy nieruchomym ciele lewy panel zmienil tylko lewe oko, a prawy tylko prawe.
  Srednia bezwzgledna zmiana macierzy ommatidiow: okolo 0.0606 po odpowiedniej
  stronie i 0 po przeciwnej. Cofniecie bodzca odtworzylo obraz bazowy.
- Uniesienie ciala o 5 mm usunelo wszystkie kontakty bez zmiany katow stawow.
  Zmiana jednego stawu o 0.1 rad i zadanie predkosci 2 rad/s zostaly poprawnie
  odczytane oraz zakodowane (domyslnie odpowiednio 40 Hz i 8 Hz).
- `sensory_model.py`: 182 jawnie opisane kanaly (8 wzrokowych, 6 kontaktowych,
  168 kat/predkosc ze znakiem). Sa to hipotezy kodowania 0-200 Hz, nie biologiczna
  mapa receptorow. Pelne obrazy ommatidiow sa zachowane do pozniejszego mapowania.
- Chod 0.3 s, warmup 0.05 s, fizyka 0.1 ms, obserwacje co 10 ms, seed 42.
  Zapisano 31 obserwacji wraz z koncowym punktem. Przez pierwsza tercje brak paneli,
  potem lewy panel, nastepnie prawy. Kontroler CPG pozostaje niezalezny od sensorow.
- Odtwarzanie offline do 182 niezaleznych neuronow LIF/Brian2 wygenerowalo
  1411 impulsow w 167 kanalach; powtorzenie identyczne, kontrola z zerowym wejsciem
  bez impulsow. To zastepcza warstwa wejsciowa, NIE 182 neurony FlyWire.
- Reset fizyki odtworzyl wszystkie zapisane stany identycznie. Obrazy renderera
  maja drobne roznice numeryczne (w sprawdzonych przebiegach ponizej 0.0001
  intensywnosci), a roznica zakodowanych czestotliwosci byla ponizej 0.001 Hz.
  Kontrole dopuszczaja odpowiednio 1/255 i 0.01 Hz; nie deklarujemy identycznosci
  bitowej obrazow miedzy renderowaniami ani zgodnosci miedzy komputerami.
- 24 testy (9 nowych) i 25 kontroli calego eksperymentu przeszlo. `pip check` bez
  konfliktow; zadne nowe pakiety ani wtyczki nie byly potrzebne.
- `visual-probes.png`, `sensory-traces.png` i `sensory-preview.mp4` pokazuja zapis
  rzeczywistego wykonania symulatora. Wideo: 30 klatek, 1200x450, 10 fps, 10-krotne
  spowolnienie. Sprawdzono wizualnie podglad oraz piksele wszystkich paneli
  odkodowanego filmu: niepuste i zmieniajace sie.

Granica etapu: nie dodano wechu, mapowania ommatidium -> FlyWire root ID, modelu
receptorow proprioceptywnych, VNC ani sterowania z mozgu. Wzrokowe kanaly ON/OFF
sa pochodna sredniej intensywnosci oka, nie odtworzeniem obwodow T4/T5.
Nie przypisano przypadkowych neuronow do wejsc ani do nog.

Uruchomienie i dokumentacja:

```powershell
.\.venv-body\Scripts\python.exe sensory_experiment.py
.\.venv-body\Scripts\python.exe -m unittest discover -s tests -v
```

Konfiguracja: `experiments/sensory-baseline.json`. Wyniki:
`outputs/flylab/sensory-stage4/`. Powtorne uruchomienie nadpisuje ten katalog;
inne miejsce wybiera `--output`. Szczegoly: `SENSORY_MODEL.md`.

## Wynik etapu 5

- `closed_loop_model.py`: audytowana para DNa03/DNa02 po obu stronach, 4 neurony,
  4 rzeczywiste skierowane pary i 645 kontaktow synaptycznych z lokalnego importu.
  Etykiety i strony zweryfikowane w `processed_labels.csv.gz` i `classification.csv.gz`;
  oba pliki odczytane do konca i zahashowane. Nie dodano sztucznych krawedzi do grafu.
- Wybor tego obwodu wynika z publikacji o sterowaniu skretem. Mapowanie sredniego
  przyciemnienia oczu bezposrednio na wejscie DNa03 jest jawna hipoteza zastepcza.
  LIF i wagi pochodza z etapu 3; nie przeprowadzono kalibracji biologicznej.
- `NeuralRuntime` w `brain_model.py`: ta sama konstrukcja sieci dla prob batch
  i petli online. Stan blony, refrakcja i kolejki synaptyczne pozostaja miedzy krokami.
  Test numeryczny potwierdza identycznosc przebiegu ciaglego i dzielonego na 1 ms.
- `closed_loop_experiment.py`: obraz -> DNa03 -> prawdziwe synapsy -> impulsy
  DNa02 -> adapter amplitudy CPG -> biomechanika -> nowy obraz. Nowa komenda
  obowiazuje dopiero w nastepnym przedziale, z jawnym opoznieniem 10 ms.
- Czas proby 0.6 s, seed 42, 6000 krokow fizyki, 60 przedzialow sterowania,
  61 obserwacji, rozgrzewka 50 ms. Katowy ruch CPG i adhezja pochodza z FlyGym;
  chod do przodu nie wymaga aktywnosci tego wycinka mozgu.
- W probie connected: 93 impulsy, w tym DNa02 lewy/prawy 28/14. Zmiana kierunku
  na koncu proby 18.612 stopnia; kontrola z odlaczonym wyjsciem 1.332 stopnia.
  Maksymalna roznica polozenia miedzy tymi probami 1.792 mm, kierunku 19.542 stopnia.
- Wyciszenie obu DNa02: 0 ich impulsow, ale DNa03 nadal aktywne. Usuniecie
  wszystkich 4 krawedzi daje takze 0 impulsow DNa02. Obydwie kontrole odtwarzaja
  dokladnie fizyke z odlaczonym wyjsciem mozgu; komenda CPG wynosi wtedy [1,1].
- Usuniecie modulacji wzrokowej przy zachowanym tonusie 20 Hz zmienia impulsy,
  komendy i trajektorie. Ruch ciala zmienia kolejne obrazy siatkowki.
- Powtorzenie connected: identyczne impulsy, skwantowane wejscia, komendy i qpos.
  Rowniez odtworzenie zapisanych wejsc w nowej sieci daje identyczne impulsy
  i stan koncowy. Retina rozni sie maksymalnie o okolo 0.000066 intensywnosci;
  nie deklarujemy bitowej powtarzalnosci renderera.
- Kazda proba pozostala na podlozu i w pozycji pionowej (minimum osi z > 0.988).
  Obliczenie 0.6 s z filmem trwalo okolo 14.3 s; to nadal tryb offline.
- Naprawiono przechowywanie wynikow monitorow Brian2: eksport posiada wlasna
  pamiec, a nie widoki na bufory usuwanej sieci. Dodano test regresji tego bledu.
- 35 testow jednostkowych/integracyjnych, 51 kontroli etapu 5 i 11 kontroli
  regresji etapu 3 przeszlo. Pelny import ponownie dal 725 impulsow, powtorzenie
  identyczne, kontrola bez bodzca bez impulsow. `pip check` bez konfliktow.
- Film 1200x420, 60 klatek, 10 fps: porownanie connected/disconnected/silenced.
  Sprawdzono wykres i obraz wizualnie oraz niepustosc i ruch kazdego panelu
  odkodowanego MP4. Stare widoki HTML pozostaja osobnymi narzedziami eksploracji.

Granica: to pierwszy techniczny most sensoryka-mozg-cialo na 4 neuronach,
nie symulacja calego biologicznego mozgu. Nie ma VNC, wechu, lotu ani mapowania
kontaktow/propriocepcji do mozgu. W chwili zakonczenia etapu 5 etapy 6-8 pozostawaly otwarte.

```powershell
.\.venv-body\Scripts\python.exe closed_loop_experiment.py
.\.venv-body\Scripts\python.exe -m unittest discover -s tests -v
.\.venv-body\Scripts\python.exe brain_experiment.py suite --output outputs/flylab/brain-after-stage5
```

Instrukcja: `CLOSED_LOOP.md`. Wyniki i film: `outputs/flylab/closed-loop-stage5/`.
Regresja modelu mozgu: `outputs/flylab/brain-after-stage5/`.

## Wynik etapu 6

- `behavior_model.py` i `behavior_experiment.py`: zamkniety plan serii, bodzce
  kierunkowe, kolizyjna przeszkoda, jednostronne pobudzenie DNa02 i wyciszenie.
  Nie zmieniono wag czterokomorkowego obwodu ani gainow adaptera CPG z etapu 5.
- Pelna seria: 44 proby po 0.8 s, seedy 42/43/44; cztery scenariusze po trzy
  warunki, szesc pobudzen i dwa powtorzenia. Nie odrzucono nieudanych zachowan.
- Zapisano stan neuronow, komendy, obserwacje sensoryczne i 8000 probek fizyki
  na probe. Arena zawiera poprzeczke 0.8 x 4 x 0.3 mm z 55 parami kolizyjnymi.
- Reakcja kierunkowa wzgledem neutralnego bodzca: 6/6 prob zgodnych z zadana
  strona. Srednia koncowa zmiana kierunku: lewy bodziec +61.90 stopnia,
  prawy -54.31 stopnia. To opis tego modelu, nie walidacja zachowania zwierzecia.
- Jednostronne pobudzenie zwiekszylo liczbe impulsow docelowego DNa02 i
  przesunelo kierunek w odpowiednia strone w kazdej z szesciu prob.
- Przeszkoda: bramke osiagnieto w 0/3 prob connected, 2/3 disconnected i
  2/3 silenced. Wynik pozostaje w raporcie: obecny obwod nie zapewnia skutecznego
  pokonywania przeszkody. Bramka mierzy przekroczenie granicy przez tulow,
  nie przejscie calego ciala. Nie stwierdzono upadkow wedlug przyjetego progu.
- Disconnected i silenced odtwarzaja identyczne qpos w kazdej z 12 par.
  Powtorzenia odtwarzaja impulsy, wejscia, komendy i qpos dokladnie;
  maksymalna roznica retiny okolo 0.0000654, ponizej tolerancji 1/255.
- Checkpoint zapisuje konfiguracje, plan i hashe kodu, danych oraz wynikow.
  Sprawdzono rzeczywiste wznowienie po pierwszej probie oraz integralnosc
  wszystkich zakonczonych prob: 206 poprawnych sum SHA256.
- 47 testow, 534 kontrole pojedynczych prob i 18 kontroli calej serii przeszlo.
  Regresja etapu 5 przeszla 51 kontroli z zachowaniem poprzednich wynikow.
  `pip check` bez konfliktow; nie instalowano nowych pakietow ani wtyczek.
- Dwa wykresy i cztery filmy pokazuja faktyczne zapisy symulatora. Wykresy oraz
  podglady sprawdzono wizualnie; filmy zdekodowano i sprawdzono pikselowo.
  Kazdy film zawiera kamere boczna i widok z gory, z odtwarzaniem 10x wolniej.

Granica: nadal cztery neurony FlyWire, zastepcze wejscia wzrokowe i kontroler
chodu zamiast biologicznego VNC. Trzy seedy nie stanowia biologicznych replik.
Etap 6 weryfikuje infrastrukture eksperymentow; kalibracja pozostaje etapem 8.
W chwili zakonczenia etapu 6 panel laboratoryjny (etap 7) pozostawal do realizacji.

```powershell
.\.venv-body\Scripts\python.exe behavior_experiment.py --resume
.\.venv-body\Scripts\python.exe -m unittest discover -s tests
```

Instrukcja i interpretacja: `BEHAVIOR_TESTS.md`. Wyniki:
`outputs/flylab/behavior-stage6/`. Regresja poprzedniego etapu:
`outputs/flylab/closed-loop-after-stage6/`. Poprzednie raporty pozostaja zachowane.

## Wynik etapu 7

- Lokalny panel HTTP laczy obraz MuJoCo, trajektorie, raster czterech neuronow,
  napiecia LIF, wejscia, komendy CPG, retine, kontakty i propriocepcje.
  Wspolna os czasu respektuje obserwacje na poczatku i napiecia na koncu binu.
- Odtwarzanie, pauza, reset czasu, kroki, suwak, tempo, wybor kamery,
  filtrowanie zapisow, eksport JSON i osobny widok wynikow kontrolowanej serii.
- Nowe proby w osobnym procesie Brian2/MuJoCo: konfiguracja scenariusza,
  warunku, seeda, czasu i pobudzenia. Pauza/wznowienie na granicy kroku
  sterowania; anulowane proby nie sa traktowane jako zakonczone wyniki.
- Archiwalne proby bez filmu mozna renderowac z zapisanych qpos i paneli,
  bez ponownego liczenia dynamiki. Archiwum etapu 6 pozostaje niezmienione.
- 59 testow Pythona oraz test end-to-end w Chrome przeszlo. Podglad desktop
  i mobile sprawdzony wizualnie i pikselowo. Wszystkie trzy widoki bez poziomego
  overflow przy szerokosciach 320, 390, 768 i 1920 px.
- Nowa proba left/connected/seed42 z pauza odtworzyla impulsy, wejscia,
  komendy, pelne qpos, zegary i napiecia etapu 6 identycznie. Sprawdzono tez anulowanie i render
  kamery dla archiwalnej proby neutralnej. Brak bledow JavaScript.
- Rozwiazano chwilowa blokade atomowej podmiany pliku statusu przy odczycie
  w Windows. Dodano ograniczone ponawianie bez naruszania starych wynikow.
- Serwer tylko na loopback, token i kontrola Origin dla zmian, scisla
  walidacja konfiguracji i zasobow. Bez nowych pakietow i zewnetrznych uslug.

Uruchomienie: `Uruchom-FlyLab.cmd` lub
`.\.venv-body\Scripts\python.exe lab_launch.py`. Domyslny adres
`http://127.0.0.1:8767`, instrukcja: `LAB_PANEL.md`.

Granica: nowe obliczenia sa offline, z postepem i odtwarzaniem po zakonczeniu,
nie strumieniem na zywo. Panel nie jest biologiczna walidacja obwodu i nie
dodaje calego mozgu, VNC ani lotu. W chwili zamkniecia etapu 7 etap 8 pozostawal otwarty.

## Wynik etapu 8

- 68 prob: 18 doboru parametru i 50 walidacyjnych/ablacyjnych z powtorzeniami.
  Seedy treningowe 101-102 i walidacyjne 201-203 sa rozlaczne. Wybor zamrozono
  przed walidacja. Domyslny profil i wczesniejsze archiwa pozostaly bez zmian.
- Sposrod redukcji kroku 0.35, 0.50, 0.65 wybrano 0.35. Sredni koszt
  predefiniowanego celu +/-30 stopni na nowych seedach spadl z 34.03 do 4.88
  deg-eq. To wynik zadania inzynierskiego, nie kalibracja biologiczna.
- Ablacje wyjscia, neuronow, wszystkich synaps, modulacji wzrokowej i dwoch
  polaczen zwrotnych rozdzielaja ich wplyw od niezaleznego kontrolera CPG.
  Odlaczenie wyjscia, wyciszenie DNa02 i usuniecie synaps daja identyczne qpos
  dla tego samego seeda. Po usunieciu modulacji wzrokowej strona panelu nie
  zmienia dynamiki. Wszystkie proby pozostaja w raporcie; nie odnotowano upadkow.
- Dwa powtorzenia: identyczne qpos, impulsy, wejscia, napiecia i komendy.
  Maksymalna roznica retiny 0.00006791, ponizej 1/255.
- Mediana calkowitego czasu swiezego pracownika: 21.15 s na 0.8 s modelu,
  czyli 26.44 s na 1 s symulacji. Maksymalna probkowana suma RSS drzewa
  pracownika okolo 507 MiB. Pomiar obejmuje importy, przygotowanie i eksport;
  nie obejmuje procesu nadrzednego ani GPU i zalezy od obciazenia komputera.
- Oddzielny benchmark 139 255 neuronow i 3 732 460 par: 50 ms modelu,
  725 impulsow, okolo 3.18 s Brian2 z kompilacja. Powtorzenie identyczne,
  kontrola bez bodzca bez impulsow. Szczyt probkowanego RSS calego pracownika
  zestawu neuronowego okolo 564 MiB. Pelny graf nie jest polaczony z cialem.
- 79 testow automatycznych, 790 kontroli prob, 22 kontrole serii i 11 kontroli
  benchmarku przeszlo. Zweryfikowano 340 artefaktow prob oraz hashe raportu,
  wykresow, danych wejsciowych i benchmarku. Wznowienie zachowalo ukonczone proby.
- Panel zawiera widok Walidacja z raportem i wykresami; publikowane podsumowanie
  nie zawiera lokalnych sciezek ani surowych wielkich zbiorow danych.

Instrukcja i protokol: `VALIDATION.md`. Pelne zapisy lokalne:
`outputs/flylab/validation-stage8/`. Podsumowanie w repozytorium:
`docs/validation-stage8.json`. Nadal brak biologicznego VNC, wechu, lotu,
pelnego mozgu w petli i potwierdzenia zgodnosci z zachowaniem zywego zwierzecia.
