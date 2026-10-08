# Etap 7 Panel laboratoryjny FlyLab

Panel laczy zapis ciala MuJoCo, bodzce, sygnaly sensoryczne, impulsy czterech
neuronow FlyWire i komendy CPG na wspolnej osi czasu. Pozwala przegladac
44 proby etapu 6 oraz uruchamiac pojedyncze nowe proby eksploracyjne.
To lokalne laboratorium modelu z zastepczym wzrokiem i sterowaniem ruchem,
nie zweryfikowana biologicznie symulacja calego mozgu.

## Uruchomienie

Dwuklik `Uruchom-FlyLab.cmd` uruchamia lub odnajduje panel i otwiera go
w przegladarce. W PowerShell, z katalogu projektu:

```powershell
.\.venv-body\Scripts\python.exe lab_launch.py
```

Domyslny adres to `http://127.0.0.1:8767`. Launcher sprawdza tozsamosc
istniejacego panelu; gdy port zajmuje inna aplikacja, wybiera wolny
z zakresu 8767..8777. Adres, PID i logi sa w `work/lab-stage7/`.
Nie trzeba instalowac pakietow. Ikony Lucide sa dolaczone lokalnie wraz
z licencja; panel nie korzysta z CDN, kont ani uslug zewnetrznych.

Do pracy diagnostycznej z widocznym terminalem:

```powershell
.\.venv-body\Scripts\python.exe lab_server.py --port 8767
```

Ctrl+C zamyka ten serwer i prosi aktywny proces o zakonczenie. Zamkniecie
karty przegladarki nie zatrzymuje serwera ani obliczen. Proces uruchomiony
przez launcher pozostaje w tle. Nie uruchamiac kilku serwerow dla tego samego
katalogu wynikow. To narzedzie lokalne, nie serwer do wystawienia w internecie.

## Zapisy

Archiwum filtruje scenariusze, warunki i seedy. Dostepne sa odtwarzanie,
pauza, powrot do poczatku, krok o jedna probke i suwak czasu. Predkosc
0.1x odtwarza 0.8 s symulacji w 8 s; zmiana predkosci nie zmienia wynikow.

Kamery pokazuja rzeczywisty renderer MuJoCo, z boku, z gory lub razem.
Dla prob bez filmu przycisk `Renderuj zapis` tworzy film z zapisanych
qpos i stanow paneli. Nie uruchamia ponownie ani fizyki, ani neuronow.
Pochodny film trafia do katalogu etapu 7, nie do archiwum etapu 6.

Trajektoria i odczyty polozenia uzywaja mm. Raster zachowuje dokladne czasy
impulsow i kolejnosc ID w pliku, ktora rozni sie od kolejnosci wejsc.
Pelne ID sa dostepne w tytule wiersza neuronu i eksporcie JSON; nigdy nie sa
konwertowane do zmiennoprzecinkowych liczb JavaScript.

Retina pokazuje srednia obu kanalow dla kazdego z 721 ommatidiow w oku,
w kolejnosci indeksow. Paski nie sa anatomiczna mapa siatkowki. Kontakt nog
jest binarnym pomiarem MuJoCo. Propriocepcja obejmuje 42 aktywne DOF w
kolejnosci zapisu: kat w radianach i predkosc w rad/s.

Eksport JSON zawiera konfiguracje, metryki, kontrole i sygnaly prezentowane
w panelu. Pelne tablice qpos, sterowanie kazdego kroku fizyki oraz dwa kanaly
ommatidiow pozostaja w oryginalnych plikach NPZ. Panel ich nie nadpisuje.

## Zegary

- Pozycja, retina, kontakty i panele sa obserwacjami w chwili `t`.
- Wejscia i zastosowana komenda dotycza przedzialu `[t, t + 0.01 s)`.
- Impulsy w liczniku obejmuja czasy scisle mniejsze od wybranej chwili.
- Napiecie LIF jest stanem z KONCA przedzialu. Dla `t = 0` panel pokazuje
  brak pomiaru, a dla `t = 0.01` pierwszy zapisany stan, nie nastepny.
- Komenda wyznaczona przez neurony w jednym przedziale obowiazuje dopiero
  w kolejnym. Zachowano opoznienie 10 ms z etapu 5.
- Film ma klatki od 0 do 0.79 s dla proby 0.8 s. Na koncowej obserwacji
  0.8 s panel jawnie oznacza obraz jako ostatnia klatke 0.79 s i komende
  jako ostatni zakonczony przedzial. Nie dorabia brakujacego obrazu.

Obliczenia sa offline. Podczas nowej proby panel pokazuje stan i postep;
zsynchronizowany obraz z sygnalami otwiera sie po zakonczeniu zapisu.
Nie ma strumieniowania pelnej aktywnosci na zywo ani gwarancji czasu rzeczywistego.

## Nowa proba

Konfiguracja obejmuje arene, podlaczenie/odlaczenie wyjscia, wyciszenie DNa02,
jednostronne pobudzenie, seed, czas 0.8/1/1.5/2 s i sile pobudzenia 50..300 Hz.
Pobudzenie DNa02 jest ograniczone do areny neutralnej; okno pozostaje
`[0.2,0.6)` s. Bodziec wzrokowy wlacza sie od 0.1 s. Zegary, wagi obwodu,
geometria przeszkody i parametry adaptera nie sa strojone z panelu.

Kazde uruchomienie dostaje nowy katalog `outputs/flylab/lab-stage7/run_<id>`.
Osobny proces chroni stan RNG Brian2 przed rownoleglymi zadaniami w jednym
interpreterze. Serwer dopuszcza tylko jedno aktywne zadanie naraz.

Pauza zatrzymuje oba silniki na granicy zakonczonego kroku sterowania.
Podczas przygotowania zadanie pauzy czeka na pierwszy punkt kontrolny.
Podczas kodowania filmu pauza w UI jest niedostepna. Przerwanie nie zapisuje
niepelnej proby jako zakonczonej; mozna uruchomic ja od nowa. Powrot do
poczatku w odtwarzaczu zeruje czas odtwarzania, nie dane symulacji.

Nie ma wznowienia uszkodzonego lub zakonczonego procesu z polowy stanu
Brian2/MuJoCo. Nowe proby sa eksploracyjne i nie sa doliczane do kontrolowanej
serii 44 prob. Widok `Wyniki serii` pomija powtorzenia w srednich i zachowuje
nieudane zachowania, w tym 0/3 dla bramki za przeszkoda w connected.

## Pliki i bezpieczenstwo

- `lab_data.py`: odczyt i weryfikacja archiwum, jednostki, walidacja parametrow.
- `lab_worker.py`: izolowana symulacja lub render zapisanych pozycji.
- `lab_server.py`: HTTP, kolejka jednej aktywnej proby i kontrola procesu.
- `lab_web/`: interfejs bez zewnetrznych zaleznosci sieciowych.
- `lab_launch.py`, `Uruchom-FlyLab.cmd`: lokalny start.
- `run_<id>/provenance.json`: konfiguracja, model, zrodlo grafu, wersje pakietow
  i hashe kodu. `trials/.../` zachowuje format zapisow etapu 6.
- `media_<id>/complete.json`: hash zrodla i filmu oraz metoda renderowania.
- `status.json` i `control.json`: stan i komenda procesu. Atomowy zapis
  ponawia podmiane pliku przy chwilowym konflikcie odczytu w Windows.

Serwer nasluchuje tylko na 127.0.0.1. Modyfikujace zadania wymagaja poprawnego
Host, tego samego Origin i losowego tokenu procesu. Sciezki, polecenia powloki
i dowolne parametry silnika nie sa przyjmowane z przegladarki. Odczyt plikow
ogranicza sie do jawnych zasobow panelu i zweryfikowanych wynikow.
To nie uwierzytelnianie przeciw innym procesom na tym samym komputerze.

## Weryfikacja 2026-10-08

- 59 testow Pythona przeszlo, w tym walidacja API, zakresy HTTP dla filmow,
  integralnosc plikow, precyzja ID, zegary, pauza, anulowanie i ponawianie
  atomowego zapisu po konflikcie dostepu w Windows.
- Playwright w Chrome: odtwarzanie, pauza, reset, pojedynczy krok, suwak,
  wybor kamer, odczyt wynikow i brak poziomego przewijania przy 390 px.
- Zrzuty przy 1440x1050, 1920x1000 i 390x844; niepuste piksele szesciu
  wykresow/widokow, rzeczywista zmiana obrazu kamery, brak bledow JavaScript.
  Wszystkie trzy widoki bez poziomego overflow przy 320, 390, 768 i 1920 px.
  Sprawdzono ostatnia klatke oraz powrot do wykresow po zmianie rozmiaru
  w innym widoku panelu.
- Nowa proba left/connected/seed42 przeszla pauze i wznowienie. Impulsy,
  wejscia, komendy, pelne qpos, zegary i napiecia sa identyczne z archiwalna
  proba etapu 6. Hashe kodu silnika, danych zrodlowych i raportu etapu 6
  pozostaly niezmienione; zweryfikowano 45 zakonczonych zapisow katalogu.
- Osobne uruchomienie anulowano; nie trafilo do katalogu zakonczonych prob.
- Wyrenderowano kamere dla neutral/connected/seed42 z archiwalnych qpos,
  bez ponownej symulacji; film przeszedl kontrole pikselowe obu widokow.

Dowody: `work/lab-stage7/browser-verification.json`, `layout-verification.json`,
`desktop.png`, `mobile.png`, `wide-desktop.png`, `mobile-experiment.png`
i `experiment-paused.png`. Pierwszy test procesu wykryl konflikt podmiany
pliku w Windows; poprawka i test regresyjny sa czescia tego etapu.

```powershell
.\.venv-body\Scripts\python.exe -m unittest discover -s tests
```

Test przegladarki: `tests/lab_browser.cjs`, wymaga Playwright dostepnego
w `NODE_PATH`, Chrome oraz dzialajacego panelu. Tworzy nowe lokalne proby
i pochodne filmy. `LAB_URL` moze wskazac inny lokalny port.

Nastepny etap to walidacja i wydajnosc: kalibracja, ablacje i rozdzielenie
wplywu obwodu neuronowego od niezaleznego kontrolera chodu. Panel nie
rozszerza jeszcze sieci, nie dodaje VNC, wechu ani lotu.
