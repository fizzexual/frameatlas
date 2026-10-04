# Validation

The normal suite generates its own small videos and exercises:

- CFR timestamps and buffered H.264 B-frame flushing at EOF.
- Native 60000/1001 clocks, VFR intervals, nonzero timestamps and duplicate image preservation.
- Refusal to overwrite datasets and explicit incomplete extraction on bad source/low disk space.
- Every PNG's integrity, exact PTS math and agreement between JSONL and SQLite.
- Bounded ranges, root confinement, region validation and image budgets.
- Separate native, preview, crop, contact-sheet, metadata and observation accounting.
- MCP initialization, ordered images, invalid arguments, notifications and clean stdout.
- HTTP routes, hostile Host/Origin headers, HEAD requests and served image counts.

The optional `--run-scale` integration test encodes 18,000 small frames at exactly 60 FPS with an exact 1/60 presentation clock and 300-second duration. All frames are extracted, rehashed and received as PNGs over a real subprocess's MCP stdio transport. The client verifies every image hash against the index and confirms there are no index gaps. It deliberately records no AI observations: an automated transport test cannot claim AI visual understanding.

Run it locally:

```sh
python -m pytest --run-scale tests/test_scale.py -q
```

The fixture is synthetic and low resolution. It verifies complete frame access at 18,000-frame scale, not high-resolution performance, video reconstruction fidelity, or any model's capacity to reason about all those images. A real source's native frame count can differ from a nominal duration × FPS calculation.

The GitHub Actions workflow runs this full scale check separately. Artifacts from private user footage are excluded from the public repository.

## Initial local results

On Windows / Python 3.12 / PyAV 19.0.1:

- Normal suite: **40 passed**, with the opt-in scale test skipped.
- Scale suite: **1 passed**; all 18,000 frames were extracted and verified, then all 18,000 MCP images were received and their decoded RGB hashes checked. 2,250 ordered batch calls. Total runtime was approximately 10 minutes 37 seconds on the development machine.
- Additional private-footage regression: 181 frames from a packet-remuxed 1920×1080 excerpt at 60000/1001 FPS. Every extracted RGB hash and exact timestamp matched decoding the corresponding frame of the original file. The full original clip was not extracted because native PNG storage would exceed available disk space.
- Browser check: jumping to frame 17,999 at 299.983333 seconds, stepping one frame back, and viewing adjacent thumbnails worked.

The scale client recorded zero AI observation declarations. Image transport validation is not a claim that an AI reviewed or understood 18,000 frames. These timings are a local test result, not a portable performance guarantee.
