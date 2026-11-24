# Hungry Ghosts — start here

**Resume at M21** on local branch `research/m21-scene-coordination` in
`decomp-m21`, based on `b855eb93fa797eded31c1849f2bb69d68470609a`.

- M21 connects title actor and timeline scheduling to the M19 publication and
  M20 timed dialogue. A fresh Japanese PCSX2 run corroborates activation, voice
  start, first glyph, and reset. The carried C path reaches natural reset
  in 3,991 frames and the menu in a separate 198-frame skip scenario.
- The same original session reaches New Game and AREA1 with cards disabled.
  A separate carried C run from the original AREA1 request matches seven
  checkpoint domains at the first active coordinator.
- [M21 results and reproduction](docs/M21.md).
  [Master handoff](MASTER_HANDOFF.md).
  [Archived start-here entries](START_HERE_HISTORY.md).

The sequence between title dialogue reset and AREA1 request is the next
integration boundary. Use the fresh title and AREA1 captures under ignored
`local/m21/` and preserve the existing M22 audio interface.
