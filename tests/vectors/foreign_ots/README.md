# A real OpenTimestamps proof made by another tool

These two files are test fixtures for `verify.py ots`, which checks any
OpenTimestamps proof, not only ours. They are the OpenTimestamps project's own
example, copied unmodified:

| file | sha256 |
|---|---|
| `hello-world.txt` | `03ba204e50d126e4674c005e04d82e84c21366780af1f43bd54a37816b6ab340` |
| `hello-world.txt.ots` | `be61ecb940db2e4a6f89fbed59c8d4f7f74fa756cbc1fad1ad7aabcb2bf59c30` |

- Source: https://github.com/opentimestamps/opentimestamps-client, `examples/`,
  at commit `5d5d325b2a7083e93a7c685a679edc3f6b8f27d7`.
- Licence: the OpenTimestamps Client is free software under the GNU Lesser
  General Public License, version 3 or (at your option) any later version. The
  files are redistributed unmodified under that licence; they are test data
  and are not part of any Orphograph download.
- What the proof says (`ots info`, offline): the file's SHA-256 is committed to
  Bitcoin block 358391's merkle root through a calendar.

Checking it on Bitcoin needs a Bitcoin node; the tests here use a stand-in
client and check only the binding, which needs none.
