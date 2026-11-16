# MechDSL mode

MechDSL mode compiles computational-mechanics problems written in LaTeX into finite-element
solver code, using [MechDSL](https://cemm2.github.io/MechDSL/). It needs the `mechdsl`
extra, which release builds include.

| Tab | What it does |
| --- | --- |
| Overview | What is installed and available |
| Compile | Turn a `% mechanics` LaTeX problem into solver code, or transpile an algorithm box |
| Artifacts | Browse generated solver code and transpiled output |

Emitting and transpiling code need no extra runtime. Running the generated solver to
verify it needs Taichi, which is not bundled because of its size: the app offers an
**Install taichi** action when you want it.

For the LaTeX directives, supported constitutive models and examples, see the
[MechDSL documentation](https://cemm2.github.io/MechDSL/).
