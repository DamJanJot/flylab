# Etap 5 Polaczenie mozgu z cialem

Lokalny eksperyment zamyka petle: obraz z oczu MuJoCo -> zastepcze kodowanie
wzrokowe -> rzeczywisty fragment polaczen FlyWire -> impulsy DNa02 -> amplituda
CPG -> ruch ciala -> nowy obraz. To test techniczny sterowania skretem, nie
odtworzenie calego mozgu ani biologicznie zwalidowanej muszki.

Chod do przodu zapewnia niezalezny kontroler CPG z FlyGym. Odlaczenie mozgu
usuwa jego modulacje skretu, ale nie zatrzymuje kontrolera referencyjnego.

## Uruchomienie

W katalogu projektu, z juz przygotowanym srodowiskiem:

```powershell
.\.venv-body\Scripts\python.exe closed_loop_experiment.py
.\.venv-body\Scripts\python.exe -m unittest discover -s tests -v
```

Parametry sa w `experiments/closed-loop.json`. Wyniki trafiaja do
`outputs/flylab/closed-loop-stage5/`; ponowne uruchomienie nadpisuje ten zestaw.
Opcje `--output`, `--config`, `--cache` i `--data-dir` pozwalaja wybrac inne
katalogi. Zrodla w `Downloads/mind` nie sa modyfikowane. Nie potrzeba nowych
wtyczek, kluczy API ani pobierania kolejnego connectomu.

Skrypt uruchamia szesc prob, eksportuje zapis i film, wykonuje kontrole oraz
konczy sie bledem, gdy ktorakolwiek kontrola nie przejdzie. Biezacy status jest
w `report.json`. Renderowanie wymaga dzialajacego lokalnego OpenGL/MuJoCo.

## Rzeczywisty obwod

Wybrane sa obie pary DNa03 i DNa02, a nie neurony o przypadkowo duzej liczbie
synaps. DNa03 -> DNa02 jest elementem opisanego obwodu sterowania kierunkiem.
Badania DNa02 wiaza jego aktywnosc m.in. ze skretem i skracaniem kroku po stronie
skretu. Nie jest to dowod, ze czterokomorkowy wycinek sam wystarcza do odtworzenia
zachowania zwierzecia.

| Typ | Strona w lokalnej klasyfikacji | FlyWire root ID | Rola w eksperymencie |
| --- | --- | --- | --- |
| DNa03 | lewa | 720575940620918789 | zastepcze wejscie sensoryczne |
| DNa03 | prawa | 720575940630085583 | zastepcze wejscie sensoryczne |
| DNa02 | lewa | 720575940629327659 | wyjscie do adaptera CPG |
| DNa02 | prawa | 720575940604737708 | wyjscie do adaptera CPG |

Kazdy start sprawdza etykiety w `processed_labels.csv.gz` oraz strone i klase
`descending` w `classification.csv.gz`. Tabele sa czytane do konca i hashowane.
Wycinek powstaje z cache etapu 3 po weryfikacji jego SHA256; niczego nie dorysowujemy.

W tej wersji danych sa 4 skierowane pary i 645 kontaktow synaptycznych:
DNa03 -> DNa02: 309 po lewej i 301 po prawej; zwrotne DNa02 -> DNa03:
16 po lewej i 19 po prawej. Wszystkie cztery komorki maja adnotacje ACH.
Zachowujemy wszystkie polaczenia wewnatrz wycinka, oryginalne liczby kontaktow
i model znaku/wag LIF z etapu 3. Nie ma w tym wycinku polaczen miedzy stronami.
Pominiete wejscia, hamowanie, pozostale DN i VNC sa istotnym ograniczeniem.

`mapping.json` zawiera etykiety, klasyfikacje, krawedzie, zrodla i hashe.
Pochodzenie wydania v783 opiera sie na lokalnym zbiorze; nie porownano hashy
surowych plikow z zewnetrznym manifestem wydania.

## Granica modeli zastepczych

Nie ma tu biologicznej mapy ommatidium -> DNa03. Wykorzystujemy przestrzenny
obraz oka, ale do sterowania sprowadzamy go do roznicy sredniego przyciemnienia.
To jawna hipoteza bodzca wysokiego poziomu, bez warstw siatkowki, T4/T5,
retinotopii i obliczen orientacji w central complex.

Niech `dL`, `dR` beda przyrostem kanalow `vision.left.dark` i
`vision.right.dark` wzgledem nieruchomego ciala po rozgrzewce, podzielonym przez
maksimum encodera 200 Hz. Wtedy:

```text
c = clip((dL - dR) / 0.15, -1, 1)
input_L = 20 + 180 * max(c, 0) Hz
input_R = 20 + 180 * max(-c, 0) Hz
```

Czestotliwosci zaokraglamy do najblizszego 1 Hz, aby ograniczyc znaczenie drobnego
szumu numerycznego renderera. Nie zapewnia to zgodnosci miedzy komputerami.
Wejscie to skoki napiecia Poissona do DNa03, nie dodatkowe synapsy z FlyWire.
Toniczne 20 Hz, preferencja przyciemnienia i wzmocnienie sa decyzjami inzynierskimi.

Impulsy DNa02 z kazdego przedzialu 10 ms przeliczamy na Hz i filtrujemy:

```text
decay = exp(-control_dt / 0.04 s)
filtered = decay * previous + (1 - decay) * spike_count / control_dt
turn = clip((filtered_L - filtered_R) / 100 Hz, -1, 1)
amplitude_L = 1 - 0.65 * max(turn, 0)
amplitude_R = 1 - 0.65 * max(-turn, 0)
```

Amplituda 0.35..1 jest bezwymiarowym wejsciem do trzech oscylatorow po danej
stronie. Czestotliwosc pozostaje referencyjna, 12 Hz. Oficjalne klasy
`CPGController`, `PreprogrammedSteps` i `make_tripod_cpg_network` FlyGym 2.1.0
generuja katy 42 aktywnych DOF i stany adhezji szesciu nog. Nie modelujemy tutaj
neuronow VNC, motoneuronow ani nerwowego sterowania poszczegolnymi miesniami.
Kontakty i propriocepcja sa rejestrowane, lecz nie steruja tym obwodem mozgowym.

## Zegary i przyczynowosc

- Czas proby 0.6 s, seed 42; rozgrzewka fizyki 0.05 s poza zegarem eksperymentu.
- Fizyka i LIF: 0.1 ms; odczyt sensorow i aktualizacja adaptera: 10 ms.
- W czasie `t` mierzymy obraz i trzymamy wyliczone wejscie przez `[t,t+10ms)`.
- Mozg i cialo przesuwaja sie o ten sam przedzial; cialo uzywa komendy znanej w `t`.
- Nowa komenda wynikajaca z impulsow tego przedzialu obowiazuje dopiero od `t+10ms`.
- Pierwsza komenda to `[1,1]`. Nie ma uzywania przyszlych impulsow do wczesniejszego ruchu.
- 61 obserwacji obejmuje stan koncowy, lecz tylko 60 przedzialow ma wejscie i komendy.

Jedna siec `NeuralRuntime` zyje przez cala probe: zachowuje napiecia, stan
synaptyczny, refrakcje, opoznione zdarzenia i RNG. Ten sam konstruktor obsluguje
teraz rowniez eksperymenty etapu 3. Brian2/NumPy ma globalny RNG procesu, wiec
nie nalezy przeplatac dwoch aktywnych runtime'ow w jednym procesie.

Etapy paneli sa wyznaczane na granicach calkowitych przedzialow: 0..100 ms
bez panelu, 100..350 ms panel lewy, 350..600 ms panel prawy. Zmiana obrazu
wynika zarowno z bodzca, jak i z ruchu samej muszki. Nie zapisujemy z gory trajektorii.

## Proby kontrolne

| Proba | Zmiana |
| --- | --- |
| connected | Pelna petla tego czterokomorkowego wycinka |
| repeat | Pelny reset i identyczny seed |
| disconnected | Mozg nadal pracuje, ale jego komenda nie dociera do CPG |
| silenced | Obie komorki DNa02 wyciszone przez cala probe, wejscia DNa03 nadal aktywne |
| no_edges | Usuniete wszystkie 4 polaczenia wycinka; te same komorki i parametry |
| no_vision | Kamery dzialaja, ale wejscie do DNa03 pozostaje stale 20 Hz |

Wyciszenie blokuje impulsy i dostarczenie sygnalu z opoznionych synaps; dotyczy
rowniez zdarzen juz bedacych w kolejce. Testy obejmuja to zachowanie. Koncowy
zapis wejscia connected jest odtwarzany offline w nowej sieci i porownywany
z impulsami oraz koncowymi napieciami. Dane monitorow sa kopiowane do wlasnych
tablic, aby usuniecie runtime'u nie uniewaznialo wynikow.

To proby techniczne jednego seeda, nie statystyczna walidacja zachowania.
Wiele seedow, dluzsze zadania, przeszkody i zdefiniowane metryki sukcesu naleza
do etapu 6. Cztery neurony nie wykorzystuja potencjalu pelnego importu 139 255
komorek, ale pozwalaja kontrolowac i audytowac pierwsze polaczenie z biomechanika.

## Zapis i podglad

- `comparison.png`: trajektorie, impulsy, wejscia wzrokowe i komendy do CPG.
- `closed-loop.mp4`, `preview.png`: trzy rzeczywiste rendery MuJoCo obok siebie;
  connected, disconnected i silenced. Film pokazuje 0.6 s w 6 s, czyli 10x wolniej.
  Liczniki DN odnosza sie do przedzialu zaczynajacego sie w czasie klatki.
- `media-validation.json`: liczba klatek i pomiary niepustosci/ruchu kazdego panelu
  po odkodowaniu MP4, bez wliczania tekstu do kontroli ruchu.
- `circuit.npz`, `mapping.json`: dokladny wycinek i dowody pochodzenia.
- `settings.json`, `report.json`: parametry, kontrole, wersje i hashe.
- `<proba>/recording.npz`: sensory, stany fizyki, zegary, wejscia, impulsy,
  napiecia na koncach przedzialow, komendy oraz 6000 celow stawow i adhezji.
- `<proba>/report.json`: pomiary pojedynczego przebiegu.
- `neural-replay.npz`: powtorzenie zapisanych wejsc w nowej sieci.

NPZ otwierac z `allow_pickle=False`. ID pozostaja stringami. `thorax_m` jest
w metrach, katy w radianach, czestotliwosci w Hz. `qpos_native` zachowuje
natywne mm/rad/kwaternion MuJoCo i nie jest jednorodnym wektorem SI.
`bin_start_s`/`bin_end_s` opisuja impulsy i utrzymywane wejscia; `time_s`
obejmuje rowniez koncowa obserwacje. To zapis wykonanej symulacji, nie interaktywny
panel laboratoryjny. Dotychczasowe HTML-e pozostaja oddzielnymi przegladarkami danych.

## Wynik weryfikacji 2026 10 08

Przeszlo 35 testow jednostkowych/integracyjnych i 51 kontroli tego zestawu.
Dodatkowe 11 kontroli poprzedniego etapu potwierdzilo zachowanie pelnego silnika
139 255 neuronow po wydzieleniu runtime'u. `pip check` bez konfliktow.

| Proba | Wszystkie impulsy | DNa02 lewy/prawy | Koncowa zmiana kierunku |
| --- | --- | --- | --- |
| connected / repeat | 93 | 28 / 14 | 18.612 stopnia |
| disconnected | 92 | 26 / 16 | 1.332 stopnia |
| silenced | 50 | 0 / 0 | 1.332 stopnia |
| no_edges | 50 | 0 / 0 | 1.332 stopnia |
| no_vision | 48 | 11 / 13 | 1.406 stopnia |

Polaczenie zmienilo trajektorie wzgledem odlaczenia maksymalnie o 1.792 mm
i kierunek maksymalnie o 19.542 stopnia. W kazdej probie muszka miala kontakt
z podlozem we wszystkich obserwacjach; minimum pionowej osi tulowia > 0.988.
Wyciszenie DNa02 i usuniecie krawedzi odtwarzaja fizyke kontroli disconnected.
Pozostaly skret referencyjny 1.332 stopnia nie jest przypisywany mozgowi.

Powtorzenie petli dalo identyczne qpos, impulsy i komendy, a niezalezny replay
zapisanych wejsc dalo identyczny stan neuronow. Obrazy siatkowki roznily sie
nieznacznie (maks. okolo 0.000066, tolerancja 1/255). Film sprawdzono wizualnie
i poprzez dekodowanie wszystkich 60 klatek. Obliczenie connected z zapisem
obrazu zajelo okolo 14.3 s na 0.6 s symulacji; nie jest to czas rzeczywisty.

## Zrodla

- [Rayshubskiy i in., eLife 2025](https://elifesciences.org/articles/102230v1):
  DNa02, kierunek skretu i polaczenia posrednie przez DNa03 (rys. 7).
- [Yang i in., Cell 2024](https://doi.org/10.1016/j.cell.2024.08.033):
  zwiazek DNa02 ze skroceniem kroku po stronie skretu. Nie kalibruje naszych gainow CPG.
- [FlyWire v783](https://zenodo.org/records/10676866): wydanie powiazane z lokalnym zbiorem.
- [FlyGym CPG](https://neuromechfly.org/tutorials/4a_cpg_controller/): kontroler chodu;
  wykonanie uzywa zainstalowanego kodu 2.1.0, nie starego API 1.x.
- `BRAIN_MODEL.md` i `SENSORY_MODEL.md`: parametry LIF, zalozenia znaku NT,
  kodowanie sensorow i ograniczenia poprzednich etapow.
