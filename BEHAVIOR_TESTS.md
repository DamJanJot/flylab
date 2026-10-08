# Etap 6 Serie eksperymentow zachowania

Etap 6 porownuje reakcje modelu z etapu 5 na bodzce kierunkowe, fizyczna
przeszkode oraz pobudzenie i wyciszenie neuronow. Nie zmienia wag, liczby
neuronow ani gainow CPG, aby nie mieszac pomiaru zachowania ze strojeniem modelu.
Statystyki dotycza symulatora z czterema neuronami FlyWire i zastepczymi
interfejsami wzroku/ruchu, nie populacji zywych muszek.

## Uruchomienie i wznowienie

W PowerShell, z katalogu projektu:

```powershell
.\.venv-body\Scripts\python.exe behavior_experiment.py
```

Wyniki: `outputs/flylab/behavior-stage6/`. Istniejacy zestaw nie jest automatycznie
nadpisywany. Po przerwaniu kontynuowac:

```powershell
.\.venv-body\Scripts\python.exe behavior_experiment.py --resume
```

Jedna sesja moze policzyc tylko ustalona liczbe nowych prob:

```powershell
.\.venv-body\Scripts\python.exe behavior_experiment.py --max-trials 1 --output outputs/flylab/my-behavior-series
.\.venv-body\Scripts\python.exe behavior_experiment.py --resume --output outputs/flylab/my-behavior-series
```

Nie uruchamiac dwoch procesow zapisujacych do tego samego katalogu. `--config`,
`--cache` i `--data-dir` zmieniaja konfiguracje, cache i lokalizacje surowych
danych. Obliczenia sa lokalne i offline, ale renderer wymaga OpenGL. Nie ma
nowych pakietow ani kluczy API; sluzy istniejace `.venv-body`.

## Zamkniety plan serii

`experiments/behavior-suite.json` okresla cala serie przed uruchomieniem.
Domyslnie: seedy 42, 43, 44; czas kazdej proby 0.8 s; rozgrzewka 0.05 s;
fizyka i LIF co 0.1 ms, odczyt obrazu i sterowanie co 10 ms. Seed ustawia
zarowno Poissona, jak i poczatkowe fazy CPG. Te dwa zrodla zmiennosci nie sa
rozseparowane statystycznie.

| Scenariusz | Bodziec i arena |
| --- | --- |
| neutral | Plaska arena, bez paneli |
| left | Lewy nieruchomy panel od 0.1 s do konca |
| right | Prawy nieruchomy panel od 0.1 s do konca |
| obstacle | Bez paneli, kolizyjna poprzeczka na drodze |

Kazdy scenariusz i seed ma trzy warunki: connected, disconnected oraz silenced.
Connected zamyka petle wzrok -> DNa03 -> rzeczywiste synapsy -> DNa02 -> CPG.
Disconnected pozostawia pracujacy mozg, ale wymusza referencyjna komende CPG
`[1,1]`. Silenced wycisza oba DNa02 przez cala probe i pozostawia wejscia DNa03.

Dodatkowo na arenie neutralnej kazdy seed ma jednostronne pobudzenie DNa02:
150 Hz w przedziale `[0.2,0.6)` s, osobno po lewej i prawej stronie. To modelowa
iniekcja impulsow, nie eksperyment optogenetyczny ani nowa krawedz connectomu.

Lacznie: 36 prob glownych + 6 pobudzen + 2 powtorzenia = **44 proby**.
Powtorzenia dotycza connected/seed42 dla lewego bodzca i przeszkody. Nie sa
dodatkowymi seedami i nie zwiekszaja liczebnosci grup. Nie wybieramy tylko
udanych przebiegow. Pilot w `behavior-pilot` jest oddzielny i nie wchodzi do serii.

Wszystkie warunki etapu 6 maja te same cztery zrodla Poissona w kolejnosci
DNa03 L/R, DNa02 L/R. Bez interwencji dwa ostatnie maja 0 Hz. Utrzymuje to
jednakowy rozmiar strumienia RNG w porownywanych warunkach. Rozni sie to od
dwoch zrodel etapu 5, dlatego liczby impulsow z obu etapow nie sa bezposrednio
tym samym przebiegiem, nawet przy takim samym seedzie.

## Fizyczna przeszkoda

Srodek poprzeczki: `[5,0,0.15]` mm. Polwymiary: `[0.4,2,0.15]` mm, czyli
rzeczywiste wymiary 0.8 x 4 x 0.3 mm. Dolna powierzchnia spoczywa na podlozu.
Kolor turkusowy pozwala rozpoznac ja w kamerze bocznej i nieruchomej kamerze
z gory. Nie jest to jedynie wizualny znacznik.

FlyGym dodaje jawne pary kolizyjne miedzy cialem a kazda geometria podloza;
poprzeczka jest dolaczona do tej samej listy przed kompilacja MuJoCo. Pomimo
zerowych masek automatycznej kolizji jawne pary nadal sa fizycznymi kontaktami.
Tak rozdzielone pary jawne i automatyczne opisuje
[dokumentacja MuJoCo](https://mujoco.readthedocs.io/en/stable/computation/index.html#collision-detection).

FlyGym 2.1.0 nie tworzy standardowych czujnikow kontaktu szesciu nog dla
swiata z wieloma geometriami podloza. `BehaviorRig` odczytuje wiec liste
kontaktow MuJoCo jednakowo w probach plaskich i z przeszkoda. Kontakt liczy
sie, gdy `dist <= 0`, jest aktywna wiez (`efc_address >= 0`) i jedna ze stron
jest geometria muszki, a druga podloza/poprzeczki. Flagi nog obejmuja segmenty
nogi; osobno zapisujemy kontakt przeszkody z segmentem innym niz noga.
Nie sa to biologiczne receptory ani pomiar sily w niutonach.

Kontakty, pozycja tulowia i pionowa os ciala sa rejestrowane po `mj_forward`
na KAZDYM kroku fizyki, czyli 8000 razy na probe. Sensorowe obrazy i impulsy
sa zapisywane na granicach 80 przedzialow; dodatkowa obserwacja koncowa nie
tworzy nastepnego wejscia. Pozostaje opoznienie komendy o jeden przedzial
10 ms opisane w `CLOSED_LOOP.md`.

## Metryki i interpretacja

- Koncowa zmiana kierunku w stopniach, po rozwinieciu kata bez skokow 360 stopni.
- Droga w plaszczyznie i postep do przodu w mm miedzy probkami fizyki.
- Srednia komenda CPG L/R i liczba impulsow obu DNa02.
- Minimum pionowej osi tulowia; wartosc ponizej 0.5 klasyfikujemy jako upadek.
- Czas i pierwsze wystapienie kontaktu z przeszkoda, osobno kontakt ciala bez nog.
- Udzial probek fizyki z kontaktem przynajmniej jednej nogi z podlozem/przeszkoda.
- Osiagniecie bramki: tulow przekroczyl `x >= 6.5 mm` przy `abs(y) <= 3 mm`.
  Sukces wymaga tez braku upadku w calej probie. Bramka NIE oznacza przejscia
  calego ciala ani inteligentnego omijania przeszkody.

Kazda grupa zawiera trzy wyniki, srednia, minimum, maksimum i odchylenie
standardowe probki. Raport pokazuje wartosci per seed, bez testow istotnosci
czy deklaracji statystycznej walidacji biologicznej.

Wplyw polaczenia mierzymy roznica connected - disconnected przy tym samym
seedzie i scenariuszu. Reakcja kierunkowa to roznica wzgledem connected/neutral
dla tego samego seeda: minimum +5 stopni dla lewego bodzca, -5 dla prawego.
To jawna umowna metryka, nie dopasowanie progu do otrzymanych wynikow.
Pobudzenie DNa02 porownujemy z neutral/connected tego samego seeda, zapisujac
zmiane kierunku i liczby impulsow neuronu docelowego.

`status: passed` oznacza poprawnosc techniczna calej serii. Nie wymaga
przejscia przeszkody ani zgodnej reakcji na wszystkie bodzce. Upadek, brak
przejscia bramki lub skret w nieoczekiwana strone pozostaja w statystykach.
Blad numeryczny, rozjechane zegary lub ostrzezenie MuJoCo przerywaja serie
jako blad techniczny; nie sa usuwane po cichu z mianownika.

## Checkpoint i integralnosc

`manifest.json` przechowuje plan, aktualna probe i hashe zakonczonych wynikow.
Jest zapisywany przez plik tymczasowy i atomowe zastapienie, tak by przerwanie
zapisu nie niszczylo poprzedniego checkpointu.

Wznowienie sprawdza konfiguracje, wersje pakietow, parametry LIF, hashe kodu,
mapowanie neuronow i kazdy zapisany artefakt zakonczonych prob. Zmiana danych,
parametrow lub kodu wymaga nowego katalogu wynikow. Nie miesza sie wynikow
roznych wersji. Nieukonczona proba jest liczona od poczatku; nie wznawiamy
stanu MuJoCo w srodku kroku. Tryb `--max-trials` zapisuje stan `partial`.

Weryfikacja obejmuje porownanie pelnego qpos w disconnected i silenced dla
kazdej pary seed/scenariusz, dwa powtorzenia pelnej petli, okna pobudzen,
bilans impulsow, granice amplitud, zegary i brak ostrzezen fizyki. Filmy sa
dekodowane i sprawdzane pikselowo osobno dla obu widokow, z wylaczeniem tekstu.

## Pliki wynikowe

- `manifest.json`, `settings.json`, `mapping.json`, `circuit.npz`: plan,
  fingerprint, konfiguracja i rzeczywisty czterokomorkowy wycinek.
- `report.json`: kompletne wyniki grup, porownania parowane i kontrole serii;
  powstaje po zakonczeniu wszystkich prob.
- `trajectories.png`: wszystkie trzy seedy i kontrole, bez wyboru najlepszej trasy.
- `outcomes.png`: roznice kierunku per seed i jednostronne pobudzenia.
- `trials/<scenariusz-warunek-seed>/result.json`: metryki i hashe jednej proby.
- `trials/<...>/physics.npz`: 8000 obserwacji fizyki z jednostkami SI dla pozycji.
- `trials/<...>/<tryb>/recording.npz`: zapis neuronow, obrazu, qpos i komend z etapu 5.
- `trials/<...>/preview.mp4` i `preview.png`: predefiniowane podglady seed42:
  connected left/right/obstacle oraz disconnected obstacle. Kazdy ma widok
  boczny i z gory. Film odtwarza 0.8 s symulacji w 8 s.

Wszystkie NPZ odczytywac z `allow_pickle=False`; identyfikatory neuronow
pozostaja stringami. `qpos_native` zawiera natywne mm/rad/kwaternion MuJoCo,
nie jest jednorodnym wektorem SI. Pliki etapow 3-5 nie sa nadpisywane przez serie.

Testy:

```powershell
.\.venv-body\Scripts\python.exe -m unittest discover -s tests -v
```

## Wynik sprawdzonej serii 2026-10-08

`outputs/flylab/behavior-stage6/report.json`: 44/44 prob zakonczonych,
534 kontrole prob i 18 kontroli serii poprawne. Caly zestaw 47 testow przeszedl;
regresja etapu 5 w `outputs/flylab/closed-loop-after-stage6` przeszla 51 kontroli.
`pip check` nie wykryl konfliktow zaleznosci. Zweryfikowano 206 sum SHA256
kodu i zapisanych artefaktow. Rzeczywiste wznowienie kontynuowalo serie od
drugiej proby bez ponownego liczenia pierwszej.

| Pomiar | Wynik |
| --- | --- |
| Kierunek skretu wzgledem neutral/connected, lewy i prawy bodziec | Zgodny w 6/6 prob |
| Srednia koncowa zmiana kierunku, connected/left | +61.90 stopnia |
| Srednia koncowa zmiana kierunku, connected/right | -54.31 stopnia |
| Pobudzenie jednostronne | Wzrost impulsow docelowego DNa02 i odpowiedni skret w 6/6 prob |
| Bramka za przeszkoda, connected | 0/3 |
| Bramka za przeszkoda, disconnected | 2/3 |
| Bramka za przeszkoda, silenced | 2/3 |
| Upadki wedlug progu pionowej osi < 0.5 | 0/44 |
| Identycznosc fizyki disconnected/silenced | 12/12 par |
| Powtorzenia left/obstacle | Impulsy, wejscia, komendy i qpos identyczne |

Katy w tabeli sa zmianami wzgledem poczatku danej proby, nie roznicami wobec
neutralnego bodzca. Ocena kierunkowa 6/6 uzywa osobnych porownan parowanych
z neutral/connected tego samego seeda. Pelne wartosci sa w `report.json`.
Maksymalna roznica obrazow retiny miedzy powtorzeniami wyniosla okolo
0.0000654 przy dopuszczalnej 1/255; obrazy nie sa bitowo identyczne.

Wynik przeszkody jest istotnym ograniczeniem, nie bledem runnera: podlaczenie
obecnego obwodu nie poprawilo tej metryki w badanych trzech seedach. Nie
dostrajano modelu do tej serii. Bramka oznacza polozenie tulowia, nie
przejscie calego ciala ani udowodnione omijanie przeszkody. Te trzy proby
nie wystarczaja do uogolnienia na inne areny, czasy i parametry.

Sprawdzono wizualnie oba wykresy i podglady renderera. Cztery zapisane filmy
maja po 80 klatek 1280x544, 10 fps; oba widoki w kazdym filmie sa niepuste
i zmieniaja sie. Pokazuja stan symulatora, nie animacje niezalezna od fizyki.

Etap 7 pozostaje osobnym zadaniem: panel laboratoryjny do przegladania tych
danych i konfiguracji. Dotychczasowe HTML-e nie steruja tym runnerem.
