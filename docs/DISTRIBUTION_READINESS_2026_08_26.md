# Distribution readiness — 2026-08-26

Preparation only. No account, package, image, listing, or submission was
created by this work.

| Rail | Current eligibility and mechanics | Effort | Decision |
| --- | --- | --- | --- |
| npm | The real zero-runtime-dependency Node SDK is packaged as `orphograph` 0.1.0. `npm run release:check` builds, runs 26 tests, inspects the tarball, installs it into a clean temporary prefix, and executes the installed CLI. Publishing requires the founder's npm login/2FA; npm also supports staged publishing with later 2FA approval. | Low | **Next rail. Founder publish.** |
| Official MCP Registry | The live `orphograph-mcp` PyPI package is eligible for a PyPI-backed server entry. The official registry uses `server.json`, `mcp-publisher`, and namespace proof through GitHub/OIDC, DNS, or HTTP. It is still documented as preview. | Low–medium | Stage after registry metadata is deduped against existing MCP directory work. |
| Homebrew tap | Anyone can create a Git-backed tap; GitHub naming convention is `homebrew-tap`. A useful formula needs a stable release tarball, SHA-256, declared Python/Node dependencies, and install test. | Medium recurring | Defer until npm/PyPI show installs; a tap adds maintenance, not a new product. |
| GHCR container | GitHub's registry supports public OCI images and repository-linked publication with `GITHUB_TOKEN`; new packages default private and need explicit visibility. Orphograph has no reviewed production Dockerfile or container support contract. | Medium–high | Defer. Do not invent an image merely to fill a catalog. |
| Docker Hub | Same missing-image problem plus a second credential/release surface. | High recurring | Reject until a container is independently demanded. |
| Bitcoin/OTS and dev-tool catalogs | These are editorial submissions, not package rails. Each requires an accurate listing, maintained support link, and manual acceptance. | Medium per directory | Use the prepared DevHunt images first; founder submits. |

## npm evidence captured

- `npm ci`: clean install.
- `npm test`: 26/26 tests pass, including Python/TypeScript Merkle parity.
- `npm pack --dry-run`: 19 files, 22.4 kB packed, 84.3 kB unpacked.
- Fresh-prefix tarball install: successful.
- Installed `orphograph --help`: successful and includes the privacy contract.

Founder command after login and a final name/version check:

```sh
cd sdk-node
npm run release:check
npm publish
```

Do not publish if `npm view orphograph` reveals a package owned by someone
else; choose an organization scope instead. Do not add registry credentials to
the repository.

## Primary references checked 2026-08-26

- npm publishing: https://docs.npmjs.com/creating-and-publishing-scoped-public-packages/
- Homebrew taps: https://docs.brew.sh/How-to-Create-and-Maintain-a-Tap
- GitHub Container Registry: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
- Official MCP Registry: https://github.com/modelcontextprotocol/registry
