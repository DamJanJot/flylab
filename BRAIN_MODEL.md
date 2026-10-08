# FlyLab: silnik neuronowy, etap 3

To lokalny model LIF w Brian2 oparty na rzeczywistych polaczeniach FlyWire.
Nie jest emulacja kompletnego zwierzecia. Warstwa zmyslow dodana w etapie 4 jest
opisana w `SENSORY_MODEL.md`. Etap 5 dodal osobny eksperyment petli z cialem na
czterokomorkowym wycinku DNa03/DNa02; opis w `CLOSED_LOOP.md`. Wejscie wzrokowe
i adapter CPG sa zastepcze, nie ma biologicznej mapy receptorow ani VNC.
Sukces testu oznacza poprawnosc techniczna, nie potwierdzenie biologicznej wiernosci.

`NeuralRuntime` zachowuje te sama siec Brian2 pomiedzy wywolaniami `advance`.
Uzywaja go zarowno ponizsze proby batch, jak i petla etapu 5; model LIF,
opoznienia i parametry nie zostaly zmienione. Runtime wymaga jednego aktywnego
zrodla RNG Brian2 na proces. Wyniki eksportu sa samodzielnymi kopiami tablic.

## Uruchamianie

W PowerShell, z katalogu projektu:

```powershell
.\.venv-body\Scripts\python.exe brain_experiment.py suite
```

Zestaw porownuje maly obwod z pelnym importem, wykonuje kontrole, pobudzenie,
wyciszenie, powtorzenie i benchmark pelnej sieci. Wyniki oraz dwa wykresy sa
w `outputs/flylab/brain-stage3`. Kazdy przebieg nadpisuje swoj katalog.
Inne miejsce mozna podac przez `--output`. Nie uruchamiac dwoch zapisow do tego
samego katalogu jednoczesnie.

Wlasny maly eksperyment, z parametrami i wybranymi sladami napiecia:

```powershell
.\.venv-body\Scripts\python.exe brain_experiment.py run experiments/brain-ct1-stimulation.json --packet outputs/flylab/prepared-baseline.json --parameters experiments/brain-parameters.json --record 720575940626979621 720575940621116807 --output outputs/flylab/brain-custom
```

Pominiecie `--packet` uzywa CALEJ lokalnej tabeli z cache. `--cache` zmienia jego
lokalizacje. Zmieniaj `interventions` w konfiguracji: `stimulate` wymaga `rate_hz`,
`silence` nie. ID musza byc stringami. Przedzialy sa polotwarte [start_s, end_s).
Czasy musza byc dokladnymi wielokrotnosciami kroku neuronowego. Jednoczesne
bodzce tego samego neuronu sumuja czestotliwosci; wyciszenie ma pierwszenstwo.

Ponowny import danych (zwykle niepotrzebny):

```powershell
.\.venv-body\Scripts\python.exe flywire_data.py --data-dir "$env:USERPROFILE\Downloads\mind"
```

Importer nic nie zapisuje w katalogu zrodlowym. Agreguje wpisy neuropili dla
kazdej skierowanej pary, bez nowego progu i bez usuwania autaps. ID pozostaja
dziesietnymi stringami, indeksy tablic sa int32, liczby kontaktow int64.
Nie buduje gestej macierzy 139255 x 139255. Brakujacy koniec polaczenia,
niepoprawne liczby, duplikaty ID i uszkodzony GZIP przerywaja import.
Nazwy i typy wizualne sprawdzane sa strukturalnie; nie steruja modelem.
Manifest zapisuje lokalne SHA256, a loader sprawdza hash archiwum cache.
Nie porownano tych hashy z opublikowanymi checksumami. Deklaracja v783 pochodzi
z kontekstu lokalnego pobrania. `complete_brain: false` jest celowe: cala tabela
progowanej lacznosci nie oznacza wszystkich synaps i mechanizmow mozgu.

## Rownania I Parametry

Poza okresem refrakcji:

```text
dv/dt = (v_rest - v + g) / tau_m
dg/dt = -g / tau_g
g_post += sign(NT_pre) * contact_count * weight_per_contact
```

W Brian2 stosowane jest dokladne rozwiazanie liniowych rownan podprogowych.
Prog: `v > -45 mV`; po impulsie `v = -52 mV` i `g = 0`.
Refrakcja 2.2 ms zamraza v i g; opoznienie synaps 1.8 ms.
Stale czasowe: blona 20 ms, zanik g 5 ms. Waga kontaktu: 0.275 mV.
`g` ma jednostke napiecia i oznacza naped synaptyczny, NIE przewodnosc.
Wszystkie pliki JSON/NPZ uzywaja sekund i woltow; wykresy ms/mV.

To parametry wzorowane na [kodzie Shiu](https://github.com/philshiu/Drosophila_brain_model/blob/main/model.py),
nie kalibracja na obecnym zestawie v783. [Publikacja](https://pmc.ncbi.nlm.nih.gov/articles/PMC11446845/)
jest punktem odniesienia. Implementacja nie jest dokladna reprodukcja pracy:

- ACH przyjmuje znak +1, GABA i GLUT -1. Sa to uproszczenia, bez receptorow
  postsynaptycznych. DA, SER, OCT i UNKNOWN maja wage 0, nie dodatnia z domyslu.
  Neuromodulacja nie jest modelowana. W cache polaczenia pozostaja zachowane.
- Zewnetrzne pobudzenie to Brian2 PoissonGroup i skok v o 68.75 mV.
  Zachowujemy refrakcje pobudzanych neuronow; kod referencyjny ustawia im 0 ms.
  To perturbacja techniczna, nie model optogenetyki ani kodowanie sensoryczne.
- PoissonGroup jest dyskretyzowany: najwyzej jeden event na krok na kanal.
  Ograniczamy rate*dt do 0.1, ale nie eliminuje to bledu dyskretyzacji.
- Wyciszenie resetuje v/g na granicy odcinka, blokuje impulsy i dostarczenie
  sygnalu do/z wyciszonego neuronu. Kod referencyjny zeruje tylko wagi wyjsciowe.
  Zdarzenia juz w kolejce sa bramkowane w chwili dostarczenia, nie emisji.
- Na wykresie v widac skok pobudzenia przed kolejnym testem progu. Nie jest to
  ksztalt biologicznego potencjalu czynnosciowego. Rzeczywiste zdarzenia modelu
  sa oddzielnie w SpikeMonitor i na wykresie rastrowym.

Model nie ogranicza hiperpolaryzacji potencjalem odwrocenia. Test CT1 osiagnal
okolo -173 mV w komorce docelowej: to sygnal koniecznej kalibracji/zmiany modelu,
nie wynik biologiczny. Nie przycinamy danych tylko po to, aby wygladaly wiarygodnie.
Docelowo potrzebne sa dane receptorowe, lepsze modele synaps, kalibracja wag
i porownanie zachowania z pomiarami; czesc walidacji pozostaje w etapie 8.

Dokumentacja silnika: [Brian2 Synapses](https://brian2.readthedocs.io/en/stable/user/synapses.html).

## Odczyt Wynikow

`report.json` zapisuje stan wykonania, konfiguracje, pochodzenie grafu, zalozenia,
wersje zaleznosci, hashe kodu i aktywnosci, czasy oraz pamiec. `parameters.json`
i `config.json` mozna ponownie podac poleceniu `run`.

W `activity.npz` (otwierac z `allow_pickle=False`):

- `neuron_ids`: mapa indeks -> dokladny FlyWire root ID.
- `spike_indices`, `spike_times_s`, `spike_counts`: impulsy i liczby na neuron.
- `record_ids`, `trace_time_s`, `voltage_v`, `synaptic_drive_v`: wybrane slady,
  do 16 neuronow; czas to krok harmonogramu Brian2, monitor w fazie `end`.
- `input_ids`, `input_indices`, `input_times_s`: zewnetrzne zdarzenia bodzca.
- `final_voltage_v`, `final_synaptic_drive_v`: stan calej sieci na koncu.

`comparison.png` porownuje kontrole, pobudzenie CT1 i to samo wejscie z wyciszeniem.
`full-activity.png` pokazuje wynik pelnej sieci: komorki aktywne sa uporzadkowane
wedlug indeksu w tablicy, a nie polozenia anatomicznego. Punkty NIE sa wizualizacja
geometrii mozgu. Populacyjny histogram ma przedzialy 1 ms.

Seed zapewnil identyczne tablice przy powtorzeniu w tym srodowisku. Nie obiecujemy
zgodnosci bitowej miedzy wersjami Brian2/NumPy ani pomiedzy backendami.
Pelna seria techniczna uzywa jednego seedu; nie jest to analiza statystyczna zachowania.

## Wydajnosc I Srodowisko

Brian2 dziala w istniejacym `.venv-body` razem z FlyGym, bez kompilatora C++ i GPU.
Budzet pamieci jest szacowany przed budowaniem sieci, obejmuje tez rejestracje
impulsow i sladow. Duze eksperymenty moga zostac odrzucone; dlugie symulacje
beda wymagac zapisu porcjami. Estymacja nie jest twardym limitem pamieci systemu.
Szczyt working set dotyczy CALEGO procesu, a probkowany RSS nie musi uchwycic maksimum.

Cache Matplotlib i importu Brian2 znajduje sie w `work/brain-cache`.
Na Windows Brian2 ma sciezke `.brian` oparta o katalog domowy; tylko na czas importu
przekierowujemy USERPROFILE do lokalnego cache i przywracamy go w `finally`.
Nie modyfikujemy ustawien konta Windows ani globalnej instalacji Pythona.

Odtworzenie srodowiska:

```powershell
python -m venv .venv-body
.\.venv-body\Scripts\python.exe -m pip install -r requirements-simulation.lock.txt
.\.venv-body\Scripts\python.exe -m pip check
.\.venv-body\Scripts\python.exe -m unittest discover -s tests -v
```

`requirements-body.lock.txt` pozostaje historycznym lockiem etapu 2.
Wspolny lock etapu 3 zawiera rowniez Brian2. Zaleznosci bezposrednie silnika
sa w `requirements-brain.txt`. W testach moze pojawic sie ostrzezenie
PyparsingDeprecationWarning wewnatrz Brian2; nie jest bledem naszych testow.
