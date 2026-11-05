# Optional dependency baseline (2026-11-05)

The actual Linux x86_64 / Python 3.12.14 evaluation environment contains
sentence-transformers 5.6.0, transformers 5.13.0, torch 2.13.0+cpu, pip 26.3 and
setuptools 84.2.1. `pip check` passes. Both PyPI and OSV scans cover all 42 installed
packages, with zero known advisory matches and zero skipped packages. The CPU
local version suffix was normalized only for advisory matching. This is a dated
advisory database result, not a guarantee against unknown vulnerabilities.

The former ST 3.4.1 / transformers 4.48.3 / torch 2.6 baseline had current
advisories and is not the release's recommended environment. Our safetensors,
explicit known-module construction and no-remote-code path avoided several
reported vulnerable paths; those mitigations were not used to dismiss updates.

`requirements-lock.txt` records the actual **Python 3.12** environment. Some
transitives require >=3.12; this is not a universal Python 3.10 lock. The core
package's Python 3.10-3.14 CI is separate from optional-model validation.
Install CPU torch first from https://download.pytorch.org/whl/cpu, then use the
lock with PyPI; the `+cpu` wheel is not hosted on PyPI. No model weights or
third-party environment binaries are included in this distribution.

The model's fixed upstream revision declares Apache-2.0. Optional dependencies
also contain MPL and bundled LGPL/GCC-runtime-exception components; they are
not all permissive-only. If distributing an environment/container or model
files, retain upstream license/NOTICE material and assess corresponding-source
and relinking obligations. This project's MIT license does not replace them.
See the installed package inventory, including bundled native-library notices.

Data conversion separately uses PyArrow 26.0.1. It was checked against PyPI/OSV
and current Apache Arrow advisories, and conversion reproduces the same corpus
bytes. This optional data-preparation package is not a core/model dependency.
Relevant advisory sources include:
- https://github.com/advisories/GHSA-jhr6-gm9c-rqjv
- https://github.com/advisories/GHSA-29pf-2h5f-8g72
- https://github.com/pytorch/pytorch/security/advisories/GHSA-63cw-57p8-fm3p
- https://security.apache.org/projects/arrow/
