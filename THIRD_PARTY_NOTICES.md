# Third-party components

The dependency lockfiles retain package versions and distribution hashes. Their licenses apply independently of this repository's MIT license.

- **parasolid-kit 0.3.17:** distribution license expression `MIT AND Apache-2.0`; source and license notices are packaged upstream. https://github.com/monozukuri-ai/parasolid-kit
- **cadquery-ocp-novtk / cadquery-ocp-proxy 7.9.3.1.1:** distribution metadata declares Apache-2.0. The included OpenCascade runtime is also subject to its LGPL-2.1 license and exception; retain its notices/source obligations when redistributing native runtime images. https://github.com/CadQuery/OCP and https://dev.opencascade.org/resources/licensing
- **NumPy 2.5.5:** BSD-3-Clause and bundled native-library notices, as distributed. https://numpy.org/doc/stable/license.html
- **Trimesh 5.2.1:** MIT. https://github.com/mikedh/trimesh
- **Modal SDK 1.6.4:** Apache-2.0; development/deployment dependency. https://github.com/modal-labs/modal-client
- **Ruff 0.17.5:** MIT; development dependency. https://github.com/astral-sh/ruff
- **Synthetic typed B-Rep fixture:** `tests/box_fixture.py` is adapted from parasolid-kit `tests/_occt_fixtures.py` at commit `b91972a1ff86d8c8b55b07516923f7c35bfedf77`; its MIT copyright/permission notice is retained in `tests/FIXTURE-LICENSE.txt`. It is not a supplier CAD file or positive Parasolid parser acceptance sample.

No CAD input, converted model or customer artwork is relicensed or published by running this code.
