# Hungry Ghosts — start here

**Resume at M21** on local branch `research/m21-scene-coordination` in
`decomp-m21`, based on `b855eb93fa797eded31c1849f2bb69d68470609a`.

- The carried C path joins M19 resource publication and M20 timed dialogue to
  the original title actors, then reaches the first AREA1 coordinator in
  1,174 post-boot units. Eight AREA1 checkpoint domains match fresh PCSX2.
  The separate ferry reload matches seven domains. The owner run accepted
  all 79 gates.
- Fresh original runs also verify title reset, controlled AREA1 movement,
  pause/resume, and return to title. The C world setup after AREA1 entry is open.
- [M21 results and reproduction](docs/M21.md).
  [Master handoff](MASTER_HANDOFF.md).
  [Archived start-here entries](START_HERE_HISTORY.md).

Next, carry the AREA1 world initialization into the first controller-driven
frame on the same guest bus. Use the private fresh captures under `local/m21/`
and the existing M22 audio interface.
