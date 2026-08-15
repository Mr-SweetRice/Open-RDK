# Real-time traction command architecture

## Goal

Make line-following motor updates react to the newest sensor decision immediately. Continuous speed commands must not accumulate as stale FIFO work, timed movement must be interruptible, and an emergency or condition-change stop must take priority over every other operation.

## Current causes of latency

1. Each `TractionModule` owns a FIFO worker queue.
2. Every `move()` call adds another job, even when a newer speed makes the older one irrelevant.
3. `move(..., duration=...)` sleeps inside the only motor worker, preventing that worker from processing replacements or stops.
4. `Motors.move()` uses `join=True` by default and waits for both queues to drain.
5. Encoder refreshes share the same CONTROL lock and currently require separate position and RPM transactions.
6. The host waits for a firmware acknowledgement for every output update, so a control loop can produce decisions faster than they can be transmitted.

## Required behavior

- Continuous speed control is state, not a list of jobs: only the newest requested output matters.
- `stop()` is urgent and invalidates every older pending or timed movement.
- A duration never blocks the communication worker.
- Position/angle operations remain explicit tracked operations and are not silently replaced by speed updates without a defined cancellation policy.
- Encoder reads remain non-blocking to callers and cannot starve motor commands.
- Existing blocking calls remain available where scripts genuinely need acknowledgement or completion.

## Step-by-step implementation

### 1. Separate continuous control from queued operations

Add a dedicated latest-value speed mailbox to each `TractionModule`. Store:

- requested signed output;
- monotonically increasing command generation;
- request timestamp;
- optional acknowledgement event/result for callers that request confirmation.

Writing a new speed replaces the unsent speed already in the mailbox. Do not append continuous speed changes to `_task_queue`.

Keep the existing operation queue only for discrete work such as configuration, PID changes, and position commands.

### 2. Introduce an explicit real-time API

Add a method such as:

```python
motor.set_speed(value, wait_for_ack=False)
motors.set_speeds({"esquerda": left, "direita": right}, wait_for_ack=False)
```

The normal line-following call must return immediately after publishing the latest desired values. Offer `wait_for_ack=True` for diagnostics, not as the control-loop default.

Decide whether `move()` delegates to this method or remains a compatibility wrapper. Document the distinction between continuous output and discrete movement.

### 3. Make the two-motor update coherent

`Motors.set_speeds()` should publish both left and right desired outputs before either caller waits. Give the pair the same generation/timestamp so logs can identify one steering decision.

The two serial links can transmit concurrently. Do not wait for the left acknowledgement before submitting the right command.

### 4. Replace blocking durations with deadlines

Do not call `time.sleep(duration)` inside a motor worker. When a timed command starts, store its deadline and schedule a stop using a cancellable timer or scheduler.

Every newer speed command increments the generation. A timer may stop a motor only if its captured generation still matches the active generation. This prevents an expired old timer from stopping a newer command.

### 5. Implement urgent, preemptive stop

`stop()` must:

1. increment the command generation;
2. clear the latest speed mailbox and cancel its duration timer;
3. invalidate pending continuous-control acknowledgements;
4. publish output zero through a priority path;
5. optionally wait only for the zero-output acknowledgement.

It must not wait for a currently active duration to expire. Define `emergency_stop()` separately if it needs stronger retry or failure-handling behavior.

### 6. Add priority to CONTROL traffic

Use at least these priorities per device:

1. stop/emergency output;
2. newest motor output;
3. position-control commands;
4. configuration commands;
5. encoder telemetry.

The serial transport remains single-owner, but the next transaction should be selected by priority rather than by arrival order alone.

### 7. Prevent encoder polling from starving control

Before sending an encoder query, check whether a stop or speed update is pending. Skip or postpone that encoder cycle when control work exists.

Add a combined firmware telemetry response containing measured RPM, absolute position, and any other required encoder values. This replaces the current two CONTROL transactions with one transaction per sample.

Keep `get_encoders()` cache-only and non-blocking for callers.

### 8. Define overload behavior

Measure command creation rate, send rate, acknowledgement latency, replaced-command count, and oldest pending age.

Under load:

- replace stale unsent speed commands;
- never replay historical steering decisions;
- report dropped/replaced counts for diagnostics;
- preserve stop commands;
- avoid unbounded queues.

### 9. Preserve compatibility deliberately

Support existing calls during migration:

```python
motors.move(value, join=False)
motors.move(value, join=True)
motors.move(value, duration=seconds)
```

Map non-blocking continuous calls to the latest-value mailbox. Map `join=True` to waiting for acknowledgement of that specific generation, not draining all future work. Implement duration through the cancellable deadline mechanism.

Do not change `move_angle()` semantics without documenting how a speed command cancels, pauses, or overrides an active position target.

### 10. Add deterministic tests

Create unit tests with a fake transport that can delay acknowledgements. Verify:

- 100 rapid speed decisions result in the first in-flight command plus the newest pending command, not 100 transmissions;
- stop bypasses queued work;
- a new command cancels an old duration stop;
- `join=True` waits for its generation only;
- left and right commands are dispatched concurrently;
- encoder polling yields to pending movement;
- communication errors do not resurrect replaced commands.

### 11. Add connected-hardware tests

Log sensor timestamp, decision timestamp, command publication, serial transmission, acknowledgement, and encoder response. Test:

- alternating line decisions faster than the serial round trip;
- stop during a long-duration move;
- continuous encoder watching during steering;
- disconnect/reconnect while commands are being replaced;
- Ctrl+C and runtime shutdown safety.

Record median, 95th-percentile, and maximum sensor-to-command latency. Confirm that latency remains bounded and no stale steering command executes after a newer decision.

## Recommended migration order

1. Add instrumentation and fake-transport tests.
2. Implement generations and the latest-speed mailbox.
3. Implement priority stop.
4. replace duration sleeps with cancellable deadlines.
5. Add paired `Motors.set_speeds()` dispatch.
6. Prioritize control over encoder polling.
7. Add combined encoder firmware telemetry.
8. Run connected tests before changing the default behavior of `move()`.

## Temporary line-follower guidance

Until this architecture is implemented:

- avoid `duration` inside the line-following loop;
- use `join=False`;
- send only when the requested output changes;
- limit the decision/send rate to what the serial link can acknowledge;
- avoid encoder polling unless the controller requires it;
- call `motors.stop()` explicitly when leaving the control loop.

These measures reduce symptoms but do not eliminate FIFO stale-command execution.
