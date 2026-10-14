#!/usr/bin/env python3
"""Discord reminder bot -- proof of concept (Python 3 only).

A small Discord bot that lets a member set reminders:

    /remind in 5m ship the release      -> fires "ship the release" in 5 min
    /remind in 2h 30m call the client   -> compound durations
    /reminders                          -> list my pending reminders
    /reminders cancel <id>              -> cancel one

Design (matches the brief):
  * Idiomatic Python 3, clear structure, docstrings on public functions.
  * Lightweight error handling: bad input fails with a message that tells the
    user exactly what went wrong (duration format, unknown id, ...).
  * The core (duration parsing, reminder store, scheduler) is DISCORD-FREE
    and fully unit-testable without a bot token. The discord.py glue is
    imported lazily and only used when `run --token ...` is invoked.

CLI:
  python PoC.py selftest               # verify parsing + store + scheduler (no token)
  python PoC.py parse "in 2h 30m"      # print parsed seconds
  python PoC.py run --token TOKEN      # start the live bot (needs a token)
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

# --------------------------------------------------------------------------- #
# Duration parsing
# --------------------------------------------------------------------------- #

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}

_PART_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)")


def parse_duration(text: str) -> int:
    """Parse a human duration ("5m", "in 2h 30m", "90s") to whole seconds.

    Raises ValueError with a message naming the offending token when the
    input cannot be read as a positive duration. A leading "in" is ignored.
    """
    if not text or not text.strip():
        raise ValueError("duration is empty")
    cleaned = text.strip()
    cleaned = re.sub(r"^in\s+", "", cleaned, flags=re.I)
    # Lenient: a bare number (or decimal) with no unit = seconds.
    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        val = float(cleaned)
        if val <= 0:
            raise ValueError(f"duration must be positive, got {text!r}")
        return int(round(val))
    parts = _PART_RE.findall(cleaned)
    if not parts:
        raise ValueError(f"could not read a duration from {text!r} "
                         f"(try e.g. '5m', '2h 30m', '90s')")
    total = 0.0
    for amount, unit in parts:
        u = unit.lower().rstrip(".")
        if u not in _UNITS:
            raise ValueError(f"unknown unit {unit!r} in {text!r} "
                             f"(allowed: s/m/h/d/w)")
        try:
            total += float(amount) * _UNITS[u]
        except ValueError as exc:
            raise ValueError(f"bad number {amount!r} in {text!r}") from exc
    if total <= 0:
        raise ValueError(f"duration must be positive, got {text!r}")
    return int(round(total))


# --------------------------------------------------------------------------- #
# Reminder store
# --------------------------------------------------------------------------- #

@dataclass
class Reminder:
    """A single pending reminder."""
    id: int
    user_id: str
    channel_id: str
    due_at: float          # epoch seconds
    text: str


class ReminderStore:
    """In-memory reminder store with an integer id sequence.

    Public surface: add(), due(), all(), get(), cancel(). All methods are
    pure with respect to external services (no I/O), so they are trivially
    unit-testable.
    """

    def __init__(self) -> None:
        self._items: dict[int, Reminder] = {}
        self._next_id = 1

    def add(self, user_id: str, channel_id: str, due_at: float,
            text: str) -> Reminder:
        """Store a reminder and return it (with its assigned id)."""
        if not text or not text.strip():
            raise ValueError("reminder text is empty")
        rem = Reminder(id=self._next_id, user_id=user_id, channel_id=channel_id,
                       due_at=due_at, text=text.strip())
        self._next_id += 1
        self._items[rem.id] = rem
        return rem

    def due(self, now: float) -> list[Reminder]:
        """Return all reminders whose due_at is at or before `now`."""
        return [r for r in self._items.values() if r.due_at <= now]

    def all(self, user_id: Optional[str] = None) -> list[Reminder]:
        """All reminders (optionally scoped to one user), ordered by id."""
        items = [r for r in self._items.values()
                 if user_id is None or r.user_id == user_id]
        return sorted(items, key=lambda r: r.id)

    def get(self, reminder_id: int) -> Optional[Reminder]:
        """Fetch one reminder by id (None if absent)."""
        return self._items.get(reminder_id)

    def cancel(self, reminder_id: int) -> bool:
        """Remove a reminder by id. Returns True if one was removed."""
        return self._items.pop(reminder_id, None) is not None

    def __len__(self) -> int:
        return len(self._items)


# --------------------------------------------------------------------------- #
# Scheduler
# --------------------------------------------------------------------------- #

class Scheduler:
    """Fire due reminders through a `send` callback.

    `send(reminder) -> None` is the transport seam: in tests it is a spy;
    in production it posts to the Discord channel. The scheduler itself has
    no notion of Discord, which keeps it testable.
    """

    def __init__(self, store: ReminderStore,
                 send: Callable[[Reminder], None],
                 clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.send = send
        self.clock = clock

    def tick(self) -> list[Reminder]:
        """Fire every reminder that is now due. Returns what was fired."""
        now = self.clock()
        fired: list[Reminder] = []
        for rem in self.store.due(now):
            self.send(rem)
            self.store.cancel(rem.id)
            fired.append(rem)
        return fired


# --------------------------------------------------------------------------- #
# Discord glue (lazy, optional)
# --------------------------------------------------------------------------- #

def build_bot(token: str):
    """Construct and return a discord.py bot with the reminder commands.

    Imported lazily so that `selftest` / `parse` work without discord.py
    installed and without a token. Raises ImportError with a helpful message
    if discord.py is missing.
    """
    try:
        import discord
    except ImportError as exc:
        raise ImportError(
            "discord.py is required to run the live bot. "
            "Install with: pip install discord.py"
        ) from exc

    intents = discord.Intents.default()
    intents.message_content = True
    bot = discord.Client(intents=intents)
    store = ReminderStore()

    @bot.event
    async def on_ready() -> None:
        print(f"logged in as {bot.user} (id={bot.user.id})")

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author == bot.user:
            return
        content = message.content.strip()
        if not content:
            return
        try:
            if content.startswith("/remind"):
                # /remind in 5m text...
                rest = content[len("/remind"):].strip()
                m = re.match(r"(in\s+)?([0-9.]+\s*[a-zA-Z]+(?:\s+[0-9.]+\s*[a-zA-Z]+)*)\s+(.*)",
                             rest, flags=re.I)
                if not m:
                    await message.reply(
                        "Usage: /remind in 5m your reminder text")
                    return
                seconds = parse_duration(m.group(2))
                text = m.group(3).strip()
                rem = store.add(str(message.author.id), str(message.channel.id),
                                time.time() + seconds, text)
                await message.reply(
                    f"Reminder #{rem.id} set: {text!r} "
                    f"in {seconds // 60 or seconds // 3600} min")
            elif content.startswith("/reminders") and "cancel" in content:
                m = re.search(r"cancel\s+(\d+)", content)
                if not m:
                    await message.reply("Usage: /reminders cancel <id>")
                    return
                rid = int(m.group(1))
                if store.cancel(rid):
                    await message.reply(f"Cancelled reminder #{rid}.")
                else:
                    await message.reply(f"No reminder #{rid} to cancel.")
            elif content.startswith("/reminders"):
                mine = store.all(user_id=str(message.author.id))
                if not mine:
                    await message.reply("You have no pending reminders.")
                    return
                lines = [f"#{r.id} due {time.strftime('%H:%M:%S', time.localtime(r.due_at))}: {r.text}"
                         for r in mine]
                await message.reply("\n".join(lines))
        except ValueError as exc:
            await message.reply(f"I couldn't do that: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the bot
            await message.reply(f"Something went wrong: {exc}")

    scheduler = Scheduler(store, lambda r: None, clock=time.time)

    @bot.event
    async def on_message_edit(*args, **kwargs) -> None:  # pragma: no cover
        pass

    return bot, scheduler, store


def run(token: str) -> None:
    """Start the live bot (blocking). Requires a valid token."""
    bot, scheduler, _store = build_bot(token)
    # A background loop that ticks the scheduler. (A real deployment would
    # use an asyncio task + a persistent store; this PoC keeps it simple.)
    import threading

    def loop() -> None:
        while True:
            try:
                scheduler.tick()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)

    threading.Thread(target=loop, daemon=True).start()
    bot.run(token)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

def selftest() -> int:
    print("== selftest: duration parsing + store + scheduler (no Discord) ==")

    # 1. Duration parsing
    cases = {
        "5m": 300,
        "in 5m": 300,
        "90s": 90,
        "2h 30m": 9000,
        "1h": 3600,
        "1d": 86400,
        "1w": 604800,
        "45": 45,               # bare number -> seconds (lenient)
        "10 min": 600,
        "1.5h": 5400,
    }
    for raw, expected in cases.items():
        got = parse_duration(raw)
        assert got == expected, f"parse {raw!r}: got {got}, want {expected}"
    print(f"  duration parsing: {len(cases)} cases OK")

    # negative / bad input raises a clean ValueError naming the token
    for bad in ["", "   ", "foo", "5x", "in 5 x"]:
        try:
            parse_duration(bad)
        except ValueError as exc:
            assert str(exc), "empty error message"
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")
    print("  bad durations raise clean ValueErrors")

    # 2. Store add / due / get / cancel / list
    store = ReminderStore()
    r1 = store.add("u1", "c1", 1000.0, "ship it")
    r2 = store.add("u1", "c1", 2000.0, "call the client")
    r3 = store.add("u2", "c2", 1500.0, "water the plant")
    assert (r1.id, r2.id, r3.id) == (1, 2, 3)
    assert len(store) == 3
    # due() respects the now boundary
    assert [r.id for r in store.due(999.0)] == []
    assert [r.id for r in store.due(1000.0)] == [1]
    assert [r.id for r in store.due(1500.0)] == [1, 3]
    assert [r.id for r in store.due(5000.0)] == [1, 2, 3]
    # per-user listing is scoped
    assert [r.id for r in store.all(user_id="u1")] == [1, 2]
    assert [r.id for r in store.all(user_id="u2")] == [3]
    assert store.get(2).text == "call the client"
    # cancel removes only that id
    assert store.cancel(2) is True
    assert store.cancel(999) is False
    assert len(store) == 2
    assert store.get(2) is None
    print("  store: add/due/get/cancel/list all correct")

    # 3. Scheduler fires due reminders through the send callback and clears them
    sent: list[Reminder] = []
    fake_clock = [0.0]
    sched = Scheduler(store, send=lambda r: sent.append(r),
                      clock=lambda: fake_clock[0])
    # advance time past r1 (id1, due 1000) and r3 (id3, due 1500)
    fake_clock[0] = 1600.0
    fired = sched.tick()
    assert [f.id for f in fired] == [1, 3], f"fired wrong set: {fired}"
    assert [s.id for s in sent] == [1, 3]
    # fired reminders are gone; r2 (id 2) was already cancelled earlier
    assert store.get(1) is None and store.get(3) is None
    assert store.all() == []
    assert len(store) == 0
    # tick again: nothing new
    assert sched.tick() == []
    assert len(sent) == 2
    print("  scheduler: fired exactly the due set, cleared them, no double-fire")

    print("== selftest PASSED ==")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest", help="verify core (no Discord token needed)")
    pc = sub.add_parser("parse", help="parse a duration to seconds")
    pc.add_argument("text")
    pr = sub.add_parser("run", help="start the live bot")
    pr.add_argument("--token", required=True)
    args = p.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "parse":
        print(parse_duration(args.text))
        return 0
    if args.cmd == "run":
        run(args.token)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
