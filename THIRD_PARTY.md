# Pochodzenie danych i zaleznosci

## FlyWire

Identyfikatory, adnotacje i liczby kontaktow w malych wejsciach testowych
pochodza z lokalnych eksportow FlyWire FAFB v783. Autor danych: FlyWire
Consortium. Punkt odniesienia: [FlyWire Whole-brain Connectome Connectivity
Data, wydanie 783.0](https://doi.org/10.5281/zenodo.10676866), oraz
[FlyWire Codex](https://codex.flywire.ai/).

Projekt agreguje wiersze neuropili do skierowanych par i wybiera podgrafy.
Nie dopisuje fikcyjnych krawedzi do obwodu sterowania. Lokalnego eksportu
CSV nie porownano ze zdalnymi checksumami calego wydania. Pelne tabele,
rekonstrukcje i modele nie sa dolaczone do repozytorium; przy ich pobieraniu
i dalszym wykorzystaniu nalezy zachowac warunki oraz cytowania zrodla.

## Silniki

- [NeuroMechFly/FlyGym](https://github.com/NeLy-EPFL/flygym): model ciala,
  sensory i referencyjny kontroler CPG. Pakiet 2.1.0 deklaruje Apache-2.0.
  Model i jego zasoby sa instalowane z pakietem, nie kopiowane do tego repo.
- [MuJoCo](https://github.com/google-deepmind/mujoco): silnik biomechaniki.
- [Brian2](https://github.com/brian-team/brian2): integracja modelu neuronowego.
- [Model Shiu](https://github.com/philshiu/Drosophila_brain_model): punkt
  odniesienia modelu LIF. Roznice i hipotetyczne parametry opisuje BRAIN_MODEL.md.

Zaleznosci Pythona sa przypiete w requirements-simulation.lock.txt i zachowuja
wlasne licencje. Projekt nie nadaje im nowej licencji.

## Ikony

Plik `lab_web/lucide.min.js` jest dystrybucja biblioteki Lucide.
Oryginalna informacja licencyjna znajduje sie w `lab_web/lucide-LICENSE`.
Zrzut `docs/laboratory.png` przedstawia rzeczywisty lokalny zapis MuJoCo
w panelu FlyLab; nie jest fotografia ani obrazem wygenerowanym do ilustracji.
