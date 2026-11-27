# Deploy from a local post-install hook

An installer places an executable and its manifest on the target, then runs
`shadowctl` as a separate process. The running broker validates and starts the
candidate, catches up on recent inputs, qualifies it on live input, and retains
the baseline during probation. The hook succeeds when its own generation
commits.

## One-time setup

1. Adapt the worker to the position/velocity contract and build it for the
   target OS and architecture. The supplied `shadow-candidate` is an example
   adapter. CMake generates its manifest from the build target.
2. Install the candidate and its manifest side by side. Use the generated
   `candidate.manifest` as the template for other artifacts. Its executable
   path is relative to the manifest directory when written as
   `./shadow-candidate`. Deploy immutable, trusted local artifacts and let
   the broker accumulate at least the manifest's `replay_samples` inputs
   before calling the hook.
3. Give the broker an existing directory owned by its effective user with
   mode `0700`, on a local filesystem under a trusted parent. Use a short
   absolute socket path in that directory. Run the installer hook as the same
   user, or invoke it under the broker's service account. On QNX, the local
   socket transport requires a running socket stack with Unix-domain socket
   support and `libsocket.so.4`.

Configure the broker's service launch once. This example uses an existing
`/var/run/shadowdeploy` directory and redirects stdout to a regular file:

```sh
shadow-demo --worker /opt/shadowdeploy/bin/shadow-worker \
  --control-socket /var/run/shadowdeploy/control.sock --samples 1000000 \
  < /dev/null > /var/log/shadowdeploy.jsonl
```

`--control-socket` starts the broker in live mode. The existing
`--candidate-manifest` and `--control-stdin` options can also be used.
They share the same deployment slot and policy. When stdout is a pipe,
the hook-enabled broker uses nonblocking, best-effort telemetry. The hook
receives its result over its own connection. The broker removes its socket
during normal shutdown. If a forced kill leaves a socket behind, confirm the
broker has stopped and remove the stale pathname before restarting. Startup
refuses to overwrite existing files or sockets. The broker serves local
connections only.

## Post-install command

The installer runs the following after installing the artifact files:

```sh
export SHADOW_CONTROL_SOCKET=/var/run/shadowdeploy/control.sock
shadowctl deploy /updates/v2/candidate.manifest --wait
```

Use `shadowctl --socket PATH deploy ...` to override the environment setting.
The command prints one JSON object on stdout for a submitted invocation. It
includes the generation assigned to this request, the policy or transport
reason, and the active output owner (`baseline`, `candidate`, or `null`
when unavailable). A refusal has a null generation. The owner in a refusal is
the broker's last known owner, and the owner in a terminal result is the
owner at that result's decision.

| Exit | `outcome` | Meaning |
| --- | --- | --- |
| 0 | `committed` | This request reached commit after probation. |
| 1 | | Invalid invocation or socket configuration. See stderr. |
| 2 | `rejected` | This request was rejected before promotion. |
| 3 | `rolled_back` | The broker returned to the baseline after promotion. |
| 4 | `refused` | The broker assigned no generation. Check `reason` for `deployment_busy` or `deployment_already_committed`. |
| 5 | `unknown` | The client timed out, lost broker contact, or could not reach it. |
| 6 | `failed` | The broker reported that output stopped during this request. |

The default wait is 30 seconds, covering connection, submission and result.
Use `--timeout-ms N` after `--wait` to set a timeout of 1–300,000 ms.
On `unknown`, the generation is present if the client received the
acknowledgment. The broker can continue the deployment after the client exits.
Reconcile against the broker's deployment events before deciding what to do
with the installed files. A rejection or rollback result is delivered after
cleanup, at which point a fresh request can try again.

After v2 commits, the current broker refuses a further stage request:

```json
{"outcome":"refused","generation":null,"reason":"deployment_already_committed","active_owner":"candidate"}
```

The [roadmap](roadmap.md#milestone-2-deploy-into-an-already-running-service)
tracks an in-service v2-to-v3 upgrade.

## Run the functional example

From a host build:

```sh
sh tools/deployment_hook_example.sh ./build
```

From the generated QNX guest image:

```sh
cd /system/bin/shadowdeploy
sh ./deployment_hook_example.sh .
```

The example calls four independent hooks against the same broker. They
produce a protocol rejection, a crash during probation followed by rollback,
a successful retry, and a refusal after commit. The script prints the four
results, checks their exit codes and preserves the broker trace in its
printed temporary directory. It uses a 200 ms sample period and a 150 ms
decision budget for functional demonstration.
