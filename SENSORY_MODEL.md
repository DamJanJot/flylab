# FlyLab: zmysly, etap 4

Stan etapu 4: 2026-10-08. Zmysly symulatora i jawny encoder dzialaja.
W opisywanym tutaj eksperymencie ruch generuje niezalezny CPG, a neurony
zastepczego wejscia NIE steruja cialem. Wech i mapowanie anatomiczne sa otwarte.
Osobny eksperyment etapu 5 wykorzystuje encoder do zastepczego wejscia DNa03
i zamyka petle sterowania skretem; szczegoly i granice w `CLOSED_LOOP.md`.

## Uruchomienie

W PowerShell z katalogu projektu:

```powershell
.\.venv-body\Scripts\python.exe sensory_experiment.py
.\.venv-body\Scripts\python.exe -m unittest discover -s tests -v
```

Inny katalog i konfiguracja:

```powershell
.\.venv-body\Scripts\python.exe sensory_experiment.py --config experiments/sensory-baseline.json --output outputs/flylab/sensory-other
```

Skrypt nadpisuje tylko wybrany katalog wynikowy. Nie zmienia danych FlyWire,
wynikow etapow 2-3 ani starego interaktywnego HTML. Nie uruchamiac rownoczesnie
dwoch zapisow do tego samego katalogu. Zaleznosci sa w istniejacym
`requirements-simulation.lock.txt`; nie trzeba instalowac nowych pakietow.

## Faktycznie Mierzone Sygnaly

`SensorRig` uzywa publicznego API [FlyGym Simulation](https://neuromechfly.org/api_reference/flygym/simulation/).
Scena powstaje przez natywny [MuJoCo MjSpec](https://mujoco.readthedocs.io/en/stable/python.html#model-editing).
W kazdej obserwacji odswiezamy pochodne stanu przez `mj_forward`.

- Wzrok: dwa renderowane obrazy 450x512 z kamer glowy, korekcja fisheye i natywne
  przeksztalcenie `Retina.raw_image_to_hex_pxls`. Kazde oko ma 721 ommatidiow.
  Nie renderujemy oczu drugi raz do zrobienia odczytu siatkowki.
- Retina: tablica `(2, 721, 2)` w skali 0..1. Ostatni wymiar ma rzadkie kanaly
  yellow/pale. W danym ommatidium aktywny jest tylko jeden z nich; trzeba je
  ZSUMOWAC, nie usredniac razem z nieaktywnym zerem. To proxy intensywnosci
  oparte na kolorach renderera, nie skalibrowana luminancja ani fototransdukcja.
- Kontakt: `get_ground_contact_info()[0] > 0`, oddzielnie dla lf/lm/lh/rf/rm/rh.
  Nie wywodzimy nacisku z wysokosci nogi. Sily kontaktu nie sa kodowane w tej wersji.
- Propriocepcja: katy stawow [rad] i predkosci katowe [rad/s] z API. Zapisujemy
  wszystkie 66 DOF, a kodujemy 42 sterowane DOF wedlug jawnej listy nazw.
  To stan mechaniczny symulatora, nie odtworzenie receptorow czucia glebokiego.
- Pozycja tulowia jest przeliczana z mm na m. `qpos_native` pozostaje w natywnych
  mieszanych jednostkach MuJoCo: mm, radiany i bezwymiarowe kwaterniony.

## Proby Kontrolne

Przy tej samej pozie i zerowej predkosci ciala pokazujemy ciemny, niezderzalny
panel po lewej, po prawej oraz ponownie brak paneli. Sprawdzamy zmiane odpowiedniego
oka i sygnalu dark, brak zmiany oka przeciwnego oraz niezmienione qpos.
Kamera podgladu jest wewnatrz areny, aby prawy panel nie zaslanial muszki.

Nastepnie unosimy cale cialo o 5 mm: wszystkie kontakty znikaja, katy nie zmieniaja
sie. Osobno przesuwamy pierwszy aktywny staw o 0.1 rad i ustawiamy jego predkosc
na 2 rad/s. Sprawdzamy zarowno odczyt, jak i docelowa czestotliwosc encodera.
Sa to kontrolowane manipulacje stanem do testu czujnikow, nie model zachowania.

Chod korzysta z rzeczywistej dynamiki MuJoCo i referencyjnego CPG. Sekwencja paneli
zajmuje trzy rowne czesci proby: brak, lewy, prawy. Zmiana zachodzi na pierwszej
probce sensorycznej na lub po granicy tercji. Nie ma wplywu na kontroler chodu.

## 182 Kanaly Zastepcze

Kolejnosc kanalow jest zapisana w `encoding.json` oraz `sensors.npz`:

| Grupa | Kanaly | Regula przed obcieciem |
| --- | --- | --- |
| Wzrok staly | bright L/R, dark L/R | srednia intensywnosc oka oraz 1 minus intensywnosc |
| Zmiana wzroku | on L/R, off L/R | dodatnia/ujemna pochodna intensywnosci, dzielona przez 10/s |
| Kontakt | 6 nog | 0 lub 1 |
| Kat | 42 dodatnie + 42 ujemne | odchylenie od pozy referencyjnej / 0.5 rad |
| Predkosc | 42 dodatnie + 42 ujemne | predkosc katowa / 50 rad/s |

Kazdy wynik jest obcinany do [0,1] i mnozony przez maksymalnie 200 Hz.
Wartosci skal sa jawne w konfiguracji. Wzorzec katow to zmierzona pozycja po
rozgrzewce danej proby. Na pierwszej probce po resecie ON/OFF sa zerowe.
Encoder nie korzysta z przyszlych probek. Przestawienie czasu wstecz lub powtorzenie
tego samego timestampu jest bledem, chyba ze najpierw wykonano `reset()`.

Globalna srednia oka traci informacje przestrzenna. Kanaly ON/OFF NIE sa
odtworzeniem T4/T5, detektorem kierunku ruchu ani modelem wzroku biologicznego.
Pelna przestrzenna macierz ommatidiow pozostaje w zapisie do dalszych prac.

## Zegary I Brian2

Domyslnie: fizyka 0.1 ms, obserwacje co 10 ms, krok neuronowy 0.1 ms.
Rozgrzewka trwa 50 ms i nie wchodzi do czasu eksperymentu. Proba 300 ms daje
31 obserwacji 0..300 ms, ale 30 przedzialow sterowania wejsciem.
Ostatnia obserwacja nie tworzy dodatkowego przedzialu.

`replay_relays` odtwarza czestotliwosci offline przez Brian2 TimedArray/PoissonGroup.
Wartosc z probki obowiazuje do kolejnej probki (zero-order hold). Nastepnie
niezalezne neurony LIF otrzymuja skoki napiecia. Stale blonowe, prog, reset i
refrakcja pochodza z `ModelParameters` etapu 3. Wejscia nie maja opoznienia,
nie ma synaps rekurencyjnych ani polaczen z FlyWire. Rate*dt <= 0.1.
Dyskretne zrodlo Poissona dopuszcza co najwyzej jedno zdarzenie na krok na kanal.

Nazwy kanalow sa etykietami zastepczymi, nie FlyWire root IDs.
`anatomical_mapping: null` i `flywire_root_ids: []` sa celowe.
Przed etapem 5 trzeba jawnie dobrac mapowanie do rzeczywistego obwodu lub oznaczyc
hipoteze mapowania, uwzgledniajac strone, retinotopie, adnotacje, jednostki i pochodzenie.
Nie laczymy dowolnie 721 punktow siatkowki z 721 przypadkowymi neuronami.

## Wyniki I Powtarzalnosc

- `report.json`, `settings.json`: status, kontrole, zegary, wersje, hashe kodu,
  artefaktow i kalibracji siatkowki. Metadane jasno oznaczaja brak petli ruchowej.
- `probes.json`, `probes.npz`: kontrolowane obserwacje, obrazy i zakodowane wejscia.
- `sensors.npz`: czas, pelne ommatidia, kontakty, katy, predkosci, qpos, pozycja,
  etykiety bodzca, rates_hz, nazwy i wzorzec stawow. Otwierac z `allow_pickle=False`.
- `encoding.json`: reguly przeliczen, parametry i brak mapowania anatomicznego.
- `sensory-relays.npz`: impulsy wejsciowe i wyjsciowe wraz z indeksami kanalow,
  liczbami impulsow i koncowym napieciem zastepczych neuronow.
- `relays-disconnected.npz`: kontrola z dokladnie zerowymi czestotliwosciami.
- `visual-probes.png`: ta sama poza, bodziec z lewej/prawej, oba zlozone oczy.
- `sensory-traces.png`: zapis wzroku, kontaktow, wybranych stawow i impulsow.
- `sensory-preview.mp4`: 30 klatek 1200x450, 10 fps, 300 ms pokazane w 3 s.
  Wideo pokazuje surowy obraz po fisheye; wykres prob pokazuje odczyt ommatidiow.
- `media-validation.json`: liczba klatek i kontrola pikseli odkodowanego filmu.

W sprawdzonej probie: 1411 impulsow, 167 aktywnych kanalow, kontrola zerowa cicha.
Powtorzenie tego samego zapisu/seed w Brian2 daje identyczne tablice. Reset fizyki
daje identyczne stany. Obrazy nie sa gwarantowane bitowo identyczne: odnotowano
roznice ponizej 0.0001 intensywnosci i 0.001 Hz po kodowaniu. Tolerancje sa
oddzielne i zapisane w raporcie: 1/255 intensywnosci i 0.01 Hz.

Przeszly 24 testy jednostkowe/integracyjne i 25 kontroli tego eksperymentu.
To dowod poprawnosci technicznej wybranych odczytow i przeliczen. Nie jest to
walidacja zachowania, statystyka wielu seedow ani dowod biologicznej wiernosci.
Wech i mapowanie receptorow pozostaja do wykonania. Techniczna petla mozg-cialo
zostala nastepnie dodana w osobnym eksperymencie etapu 5, opisanym w `CLOSED_LOOP.md`.
