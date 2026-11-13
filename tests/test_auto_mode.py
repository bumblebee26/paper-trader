"""Auto mode: the suggestion decides, the user rates with 👍/👎, every limit still applies."""
import unittest
from datetime import datetime
from unittest import mock

from helpers import USER_ID, Env, minute_bars

import config
import engine
import storage
import telegram_bot as tg
import reviewer
import track

DAY = datetime(2026, 11, 12, 10, 40)
REVIEW = {"yes_pct": 60, "verdict": "YES", "why": "Banks and Nifty are both up."}


def journal_rows(event):
    return [r for r in storage.read_rows("journal/2026-11-12.csv") if r["event"] == event]


class AutoModeTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "AUTO_MODE_DEFAULT", True)
        self.addCleanup(p.stop)
        self.start = p

    def env(self, when=DAY):
        env = Env(when)
        env.__enter__()
        self.addCleanup(env.__exit__, None, None, None)
        self.start.start()                       # after Env, so auto mode is on for these tests
        return env

    def propose(self, env, st, symbol="HDFCBANK", yes=95, price=1600.0, review=REVIEW):
        env.prices[symbol] = price
        with mock.patch.object(reviewer, "review", return_value=review):
            return self._propose(st, symbol, yes, price)

    def _propose(self, st, symbol, yes, price):
        return engine.propose(st, symbol, "BUY", "ORB long", price, price * 0.99, price * 1.02,
                              ["broke the opening range on 2x volume"], "back below 1590",
                              summary="Banks are rising with the market.", ai_yes_pct=yes,
                              ai_reason="Strong breakout in a rising sector.")

    def test_high_suggestion_is_placed_without_asking(self):
        env = self.env()
        st = engine.load()
        r = self.propose(env, st, yes=95)
        self.assertEqual(r["status"], "approved")
        msg = env.sent[-1]
        for part in ("AUTO · BUY HDFCBANK (bet on a rise) · ORB long: PLACING",
                     "YES 72% · checks 60 · agent 95 · reviewer YES 60",
                     "Auto: placing it (over 55%, agent over 50%, reviewer checked)",
                     "🎯 1,632.00 (+Rs 1,984) · 🛑 1,584.00 (-Rs 992) · R:R 2.0",
                     "For: broke the opening range on 2x volume", "Agent: Strong breakout in a rising sector.",
                     "Reviewer: Banks and Nifty are both up."):
            self.assertIn(part, msg)
        self.assertLessEqual(len(msg.splitlines()), 17)                 # compact: every number, short reasons
        full = env.full()
        for part in ("Why proposed:", "- Banks are rising with the market.", "Why 72% YES",
                     "- Trading agent 95%: Strong breakout in a rising sector.",
                     "- Independent reviewer YES 60%: Banks and Nifty are both up."):
            self.assertIn(part, full)
        self.assertEqual(engine.execute(st, r["proposal_id"])["status"], "filled")
        self.assertIn("mode auto", journal_rows("proposed")[0]["note"])

    def test_low_suggestion_is_skipped_and_its_outcome_recorded(self):
        env = self.env()
        st = engine.load()
        r = self.propose(env, st, yes=5)
        self.assertEqual(r["status"], "skipped_auto")
        self.assertIn("skipped", env.sent[-1])
        self.assertEqual(journal_rows("rejected")[0]["user_reply"], "AUTO-NO")
        self.assertEqual(engine.execute(st, r["proposal_id"])["status"], "refused")
        env.bars[("HDFCBANK", "1m")] = minute_bars(datetime(2026, 11, 12, 10, 41), [(1600, 1633, 1599, 1632)])
        env.clock = datetime(2026, 11, 12, 15, 20)
        engine.report(st)
        self.assertEqual(journal_rows("passed_outcome")[0]["user_reply"], "auto_skipped")

    def test_agent_against_or_no_reviewer_means_no_auto_trade(self):
        env = self.env()
        st = engine.load()
        # today's SBIN case: checks 95%, agent 38%, no reviewer: the average was 66% but Claude was against it
        with mock.patch.object(config, "AUTO_MIN_YES_PCT", 40.0):   # the average alone would have traded it
            r = self.propose(env, st, yes=38, review=None)
        self.assertEqual(r["status"], "skipped_auto")
        self.assertIn("the trading agent itself isn't for it (38%, needs over 50%)", env.sent[-1])
        r = self.propose(env, st, symbol="TCS", yes=80, price=3000.0, review=None)
        self.assertEqual(r["status"], "skipped_auto")
        self.assertIn("the independent reviewer didn't answer", r["reason"])
        r = self.propose(env, st, symbol="RELIANCE", yes=80, price=1500.0)
        self.assertEqual(r["status"], "approved")

    def test_buttons_survive_the_placed_update_and_reactions_count(self):
        env = self.env()
        st = engine.load()
        r = self.propose(env, st, yes=95)
        engine.execute(st, r["proposal_id"])
        self.assertIn("-> Placed @", env.edits[-1])
        rows = [[b[0] for b in row] for row in env.edit_buttons[-1]]
        self.assertEqual(rows, [["👍 good call", "👎 bad call", "EXIT"], ["📈 Groww", "NSE quote", "ℹ️ Full reasons"]])
        msg_id = st["proposals"][r["proposal_id"]]["message_id"]
        env.react(msg_id, "👎")
        env.react(msg_id, "🔥")                                  # other emoji: ignored
        env.react(msg_id + 999, "👍")                            # not a proposal message: ignored
        tg.poll(st)
        engine.answer_requests(st)
        fb = journal_rows("user_feedback")
        self.assertEqual([(x["proposal_id"], x["user_reply"]) for x in fb], [(r["proposal_id"], "dislike")])

    def test_ratings_on_older_messages_and_removing_a_rating(self):
        env = self.env()
        st = engine.load()
        r = self.propose(env, st, yes=95)
        msg_id = st["proposals"][r["proposal_id"]]["message_id"]
        engine.save(st)
        env.clock = datetime(2026, 11, 16, 20, 0)               # the following Monday evening
        st = engine.load()
        self.assertNotIn(r["proposal_id"], st["proposals"])     # yesterday's proposals are gone...
        env.react(msg_id, "👍")                                  # ...but the message still maps to its trade
        tg.poll(st)
        engine.answer_requests(st)
        mon = [x for x in storage.read_rows("journal/2026-11-16.csv") if x["event"] == "user_feedback"]
        self.assertEqual((mon[-1]["proposal_id"], mon[-1]["user_reply"], mon[-1]["symbol"]),
                         (r["proposal_id"], "like", "HDFCBANK"))
        env.updates.append({"update_id": len(env.updates) + 1, "message_reaction": {
            "user": {"id": int(USER_ID)}, "message_id": msg_id,
            "old_reaction": [{"type": "emoji", "emoji": "👍"}], "new_reaction": []}})
        tg.poll(st)
        engine.answer_requests(st)
        mon = [x for x in storage.read_rows("journal/2026-11-16.csv") if x["event"] == "user_feedback"]
        self.assertEqual(mon[-1]["user_reply"], "none")         # removed: no longer rated
        env.clock = datetime(2026, 11, 24, 10, 0)               # 12 days later: too old to rate by reaction
        st = engine.load()
        self.assertNotIn(str(msg_id), st["msg_index"])

    def test_feedback_and_exit_buttons(self):
        env = self.env()
        st = engine.load()
        r = self.propose(env, st, yes=95)
        engine.execute(st, r["proposal_id"])
        env.tap("fb:like", r["proposal_id"])
        env.tap("x", r["proposal_id"])
        env.prices["HDFCBANK"] = 1605.0
        engine.monitor(st)
        fb = journal_rows("user_feedback")
        self.assertEqual((fb[0]["proposal_id"], fb[0]["user_reply"]), (r["proposal_id"], "like"))
        self.assertEqual(st["proposals"][r["proposal_id"]]["feedback"], "like")
        self.assertNotIn("HDFCBANK", st["positions"])            # EXIT button closed it
        self.assertEqual(st["decisions"], {})                    # a like is never an approval

    def test_the_saved_rating_shows_on_the_message(self):
        env = self.env()
        st = engine.load()
        r = self.propose(env, st, yes=5)                         # skipped: rating buttons only
        msg_id = st["proposals"][r["proposal_id"]]["message_id"]
        env.tap("fb:like", r["proposal_id"])
        tg.poll(st)
        engine.answer_requests(st)
        self.assertEqual(env.button_sets[-1][0], msg_id)
        self.assertEqual(env.button_sets[-1][1][0], [("✅ 👍 good call", f"fb:like:{r['proposal_id']}"),
                                                     ("👎 bad call", f"fb:dislike:{r['proposal_id']}")])
        env.tap("fb:dislike", r["proposal_id"])                  # changing your mind moves the tick
        tg.poll(st)
        engine.answer_requests(st)
        self.assertEqual([b[0] for b in env.button_sets[-1][1][0]], ["👍 good call", "✅ 👎 bad call"])
        r2 = self.propose(env, st, symbol="TCS", yes=95, price=3000.0)
        engine.execute(st, r2["proposal_id"])
        env.react(st["proposals"][r2["proposal_id"]]["message_id"], "👍")   # a reaction ticks it too
        tg.poll(st)
        engine.answer_requests(st)
        self.assertEqual([b[0] for b in env.button_sets[-1][1][0]], ["✅ 👍 good call", "👎 bad call", "EXIT"])

    def test_commands_toggle_auto_mode(self):
        env = self.env()
        st = engine.load()
        env.text("/auto off")
        tg.poll(st)
        engine.answer_requests(st)
        self.assertFalse(engine.auto_on(st))
        self.assertIn("Auto mode OFF", env.sent[-1])
        self.assertEqual(self.propose(env, st)["status"], "pending")   # human mode again
        env.text("/auto on")
        tg.poll(st)
        engine.answer_requests(st)
        self.assertTrue(engine.auto_on(st))
        self.assertEqual(len(journal_rows("auto_mode_on")), 1)

    def test_six_trade_cap_and_weekly_brake_turns_it_off(self):
        env = self.env()
        st = engine.load()
        st["trades_today"] = 6
        ok, why, *_ = engine.risk_gate(st, "TCS", "BUY", 3000.0, 2970.0, 3060.0)
        self.assertFalse(ok)
        self.assertIn("all 6 trades for today are used (auto mode)", why)
        st["trades_today"] = 0
        with mock.patch.object(config, "WEEKLY_LOSS_LIMIT_R", 8.0):
            st["week"]["realized"] = -9000.0
            engine.check_weekly_brake(st)
        self.assertFalse(engine.auto_on(st))
        self.assertIn("Auto mode is now OFF because of the weekly brake", env.sent[-1])

    def test_midday_summary_once(self):
        env = self.env(datetime(2026, 11, 12, 12, 31))
        st = engine.load()
        engine.monitor(st)
        engine.monitor(st)
        self.assertEqual(sum("Midday summary" in m for m in env.sent), 1)

    def test_track_record_shown_with_counts(self):
        env = self.env()
        st = engine.load()
        track._cache = [
            {"dimension": "setup", "value": "ORB long", "trades": 11, "wins": 3, "win_rate_pct": 27.3,
             "avg_outcome_rs": -85.0, "total_outcome_rs": -935.0},
            {"dimension": "yes_band", "value": "70-79%", "trades": 4, "wins": 3, "win_rate_pct": 75.0,
             "avg_outcome_rs": 120.0, "total_outcome_rs": 480.0},
        ]
        self.propose(env, st, yes=95)
        msg = env.sent[-1]
        self.assertIn("Track: ORB long 3/11 (avg -Rs 85) · 70-79% 3/4", msg)
        self.assertIn("ORB long: won 3 of 11, average -Rs 85", env.full())
        self.assertIn("Suggestions of 70-79%: won 3 of 4, average +Rs 120 (too few to judge)", env.full())
        judged = {r["value"]: r["judged"] for r in track.for_claude()}
        self.assertEqual(judged, {"ORB long": True, "70-79%": False})

    def test_minimums_must_be_beaten_not_met(self):
        self.env()
        st = engine.load()
        why, bar, _ = engine._auto_skip_reason(st, "TCS", "SELL", "PDL break", None, "11", 55, 50, REVIEW)
        self.assertIn("needs to be over 55%", why)               # today's TCS: exactly 55% is a coin flip
        why, *_ = engine._auto_skip_reason(st, "TCS", "SELL", "PDL break", None, "11", 56, 50, REVIEW)
        self.assertIn("isn't for it (50%, needs over 50%)", why)
        self.assertEqual(engine._auto_skip_reason(st, "TCS", "SELL", "PDL break", None, "11", 56, 51, REVIEW)[0], "")

    def test_losing_groups_raise_the_bar(self):
        self.env()
        st = engine.load()
        track._cache = [{"dimension": "mood", "value": "choppy", "trades": 14, "wins": 4, "win_rate_pct": 28.6,
                         "avg_outcome_rs": -99.0, "total_outcome_rs": -1386.0},
                        {"dimension": "setup", "value": "PDL break", "trades": 4, "wins": 1, "win_rate_pct": 25.0,
                         "avg_outcome_rs": -94.0, "total_outcome_rs": -376.0}]     # too few to count
        why, bar, bar_why = engine._auto_skip_reason(st, "TCS", "SELL", "PDL break", "choppy", "11", 58, 70, REVIEW)
        self.assertEqual(bar, 60.0)
        self.assertIn("+5: choppy mood losing", why)
        self.assertEqual(engine._auto_skip_reason(st, "TCS", "SELL", "PDL break", "trending up", "11", 58, 70,
                                                  REVIEW)[0], "")

    def test_flipping_a_stock_needs_60(self):
        env = self.env()
        st = engine.load()
        self.propose(env, st, symbol="TCS", yes=5, price=3000.0)        # a BUY idea on TCS this morning (skipped)
        why, bar, _ = engine._auto_skip_reason(st, "TCS", "SELL", "PDL break", None, "11", 58, 70, REVIEW)
        self.assertEqual(bar, 60.0)
        self.assertIn("flips an earlier TCS proposal today", why)
        self.assertEqual(engine._auto_skip_reason(st, "TCS", "BUY", "VWAP reclaim", None, "11", 58, 70, REVIEW)[0], "")

    def test_full_reasons_button_replies_with_the_long_version(self):
        env = self.env()
        st = engine.load()
        r = self.propose(env, st, yes=95)
        env.tap("info", r["proposal_id"])
        tg.poll(st)
        engine.answer_requests(st)
        self.assertIn("Why proposed:", env.sent[-1])
        self.assertIn("In plain words:", env.sent[-1])
        engine.save(st)
        env.clock = datetime(2026, 11, 16, 10, 0)                          # days later: read back from storage
        st = engine.load()
        env.tap("info", r["proposal_id"])
        tg.poll(st)
        engine.answer_requests(st)
        self.assertIn("Why proposed:", env.sent[-1])

    def test_day_report_lists_each_skipped_trade(self):
        env = self.env()
        st = engine.load()
        self.propose(env, st, yes=5)
        env.bars[("HDFCBANK", "1m")] = minute_bars(datetime(2026, 11, 12, 10, 41), [(1600, 1633, 1599, 1632)])
        env.clock = datetime(2026, 11, 12, 15, 20)
        engine.report(st)
        self.assertIn("- BUY HDFCBANK (skipped by auto mode): +Rs", env.sent[-1])


class LinksTest(unittest.TestCase):
    def test_groww_and_nse_buttons(self):
        import links
        self.assertEqual(links.groww("TCS"), "https://groww.in/stocks/tata-consultancy-services-ltd")
        self.assertEqual(links.nse("M&M"), "https://www.nseindia.com/get-quotes/equity?symbol=M%26M")
        row = links.buttons("GODREJIND", "abc")                  # not in the list: NSE only
        self.assertEqual([b[0] for b in row], ["NSE quote", "ℹ️ Full reasons"])
        kb = tg._keyboard([[("👍", "fb:like:abc")], row])
        self.assertEqual(kb["inline_keyboard"][1][0], {"text": "NSE quote",
                                                       "url": "https://www.nseindia.com/get-quotes/equity?symbol=GODREJIND"})
        self.assertEqual(kb["inline_keyboard"][0][0], {"text": "👍", "callback_data": "fb:like:abc"})

    def test_reviewer_reason_is_cut_at_a_word(self):
        cut = reviewer.shorten("the reward being twice the risk is not enough to make up for this weak backing", 70)
        self.assertTrue(cut.endswith("…"))
        self.assertNotIn("backi", cut)
        self.assertLessEqual(len(cut), 71)


if __name__ == "__main__":
    unittest.main()
