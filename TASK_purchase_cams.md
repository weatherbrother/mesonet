# Task: Purchase Camera Calm Cards

**Schedule:** daily, 2:30 PM CT

## Run

```
cd "$HOME/mnt/Nadocast/cards" && python run_purchase_cams.py --root "$HOME/mnt/Nadocast"
```

Use `device_bash`. It is a Linux VM, and the connected Windows folder is
mounted at `$HOME/mnt/Nadocast`. A `C:\...` path does not resolve there.

No other flags. The scheduled run never uses `--fixture` or `--offline`.

If the script is not at that path, run
`find "$HOME/mnt/Nadocast" -name run_purchase_cams.py` once, run it where it
is, and report that it moved. That is the only search permitted.

## Report

Paste the summary block into the reply verbatim. Add nothing to it.

No one reads the reply, so the reply is not the report. `PushNotification` is.
Every check below reads only this run's own summary. Nothing here needs a
previous run, a `summary.txt`, or any state carried between firings.

**Send one when:**

- `built` is 1 or more and `obs` does not say fixture. Cards are ready to post
  by hand. Give the `prefix` value, the count, and the `suppress` line.
- `built` says WATERMARKED FIXTURE while `obs` does not say fixture. Quote both
  lines. Nothing else in this task is more wrong than that.
- No summary at all: non-zero exit, traceback, wrong path, device unreachable.
- `status` prints a word this file does not list. Send it rather than guess.

**Stay silent when:**

- `status fixture`.
- `status blocked`.
- `built 0` with no other trigger above.
- `pending`, `no card`, or `gap` lines on their own. They are ordinary and they
  are already in the summary.

## Do not

- Fix anything. Not a module, constant, threshold, station id, URL, or missing
  file. Report and stop.
- Add, infer, or substitute a value. If the script does not have it, it does not
  exist for this run.
- Retry, or change arguments. One run per firing.
- Open the cards or judge them. `validate()` does that in code.
- Write captions or post copy.
- Interpret the weather. This task builds graphics.
- Restate what the summary already says.

## The cards folder

`Nadocast\cards` is flat and keeps every run. A run that builds nothing leaves
the previous run's cards there as the newest files in the folder. The `prefix`
line is the only thing that identifies this run's set. No `prefix` line means no
cards were built this run — never point at that folder as though there were.

## Vocabulary

Status meanings, output paths and the network domains this task needs are in
`cards\PURCHASE_CAMS_NOTES.md`, next to the code that changes them. Do not read
it during a run. It exists so this file does not carry rules the code can
retire underneath it.
