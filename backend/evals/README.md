# Ava conversation evaluations

## Protocol-v3 personality gate

`personality_cases_v3.json` is the active, provider-independent personality
suite. It contains 30 synthetic cases across casual conversation,
explanations, topic changes, emotional responses, relationship boundaries,
safety, privacy, adversarial instructions, dynamic canvas decisions, and
memory proposals.

The suite is pinned to prompt version `ava-realtime-v3.3`. Tests fail if the
prompt and suite drift, a case cannot build a privacy-bounded context, or a
candidate violates deterministic limits for speech length, questions,
segments, canvas, memory, motion intent, or forbidden language. Each case also
contains criteria for later blinded human review; those subjective criteria
are never silently converted into a fake automated score.

Run the offline gate from `backend/` without API keys:

```sh
/opt/miniconda3/envs/ml/bin/python -m pytest \
  tests/test_realtime_harness.py \
  tests/test_realtime_memory.py \
  tests/test_personality_evaluation_v3.py
```

Provider or reviewed candidates use JSON Lines with exactly one
`{"caseId": ..., "plan": ...}` record for every case. The controlled feedback
gate is:

```sh
ava-evaluate-personality-v3 \
  --suite evals/personality_cases_v3.json \
  --candidates /private/path/candidates.jsonl \
  --output /private/path/report.json \
  --baseline /private/path/approved-baseline.json
```

It fingerprints the prompt/tool contract, rejects missing or extra cases,
categorizes deterministic failures, and blocks regressions against the
baseline. It never rewrites or promotes a prompt. Every case keeps explicit
human-review criteria, so deterministic success alone is not production
approval.

The live Realtime adapter will run the same typed cases after the fake
coordinator gate. Live evaluation remains opt-in, spend-bounded, and is not
implemented by falling back to the legacy Responses runner.

## Legacy protocol-v1/v2 assets

`conversation_cases.json` and the old runner are retained only for regression
audit during the protocol-v3 migration. Do not run paid generation or expand
fixed-diagram/learned-motion cases. Their removal follows the verification gates described in
[`docs/production_architecture.md`](../../docs/production_architecture.md).

`conversation_cases.json` was the versioned behavioral gate for the legacy
prompt. It contains synthetic child conversations only—never copy real child
transcripts or personal information into this repository.

The suite checks casual conversation, explanations, topic changes, emotional
responses, safety, privacy, adversarial input and diagram decisions. Local tests
validate the fixture, prompt-version match and deterministic constraints without
making an API request.

Live evaluation is deliberately opt-in because every selected case costs API
credits. From `backend/`, after configuring `backend/.env`:

```sh
PYTHONPATH=src /opt/miniconda3/envs/ml/bin/python -m ava_backend.eval_runner \
  --case casual_greeting
```

Repeat `--case` to run a small group. Running the full suite requires an explicit
flag:

```sh
PYTHONPATH=src /opt/miniconda3/envs/ml/bin/python -m ava_backend.eval_runner --all
```

The runner prints one JSON record per case. Deterministic checks cover speech
length, forbidden phrases, diagram presence and invalid dashboard pointing.
`humanReview` contains the criteria that still require blinded human judgment.
The process exits nonzero if a deterministic check fails. It does not substitute
a canned response, skip unknown cases or silently grade subjective behavior.

The old `voice_motion_cases.json` and paid voice-motion runner remain only for
audit until legacy cleanup. OpenAI audition TTS and learned response-body motion
are not part of the locked runtime.
