"""Host wiring for the real Penhin Agent event API; no credentials in this file.

Pass make_responder(model, session, runtime) as VoiceInteraction's respond.
The host supplies an authenticated ModelClient, an authorized ASR AudioInput,
and a local or remote AudioSink. Call voice.interrupt() on barge-in.
"""

import asyncio

from penhin import Agent, CancellationToken

from penhin_tts import BackendError


def make_responder(model, session, runtime=None):
    async def respond(prompt):
        queue = asyncio.Queue(maxsize=1)
        accepting = True
        token = CancellationToken()

        async def observe(event):
            if event.kind != "text_delta" or event.session_id != session.id:
                return
            # An SDK callback can outlive Agent cancellation. Never leave it
            # waiting on a queue whose consumer has already closed.
            while accepting and queue.full():
                await asyncio.sleep(0.01)
            if accepting:
                queue.put_nowait(event.data)

        agent = Agent(model, plugins=runtime, observe=observe)
        task = asyncio.create_task(agent.run(session, prompt, cancellation=token))
        try:
            while True:
                if task.done():
                    result = task.result()
                    if result.status == "cancelled":
                        raise asyncio.CancelledError()
                    if result.status != "complete":
                        raise BackendError("answer_not_complete")
                if not queue.empty():
                    yield queue.get_nowait()
                elif task.done():
                    return
                else:
                    await asyncio.wait({task}, timeout=0.01)
        finally:
            accepting = False
            token.cancel()
            if not task.done():
                task.cancel()
            # Repeated host cancellation must still wait for Agent cleanup.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
                except Exception:
                    break
            if not task.cancelled():
                task.exception()

    return respond
