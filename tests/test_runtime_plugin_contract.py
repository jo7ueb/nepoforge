"""v2.4.2 plugin/API static contract tests; no OpenCode runtime is started."""
from pathlib import Path
import unittest


PLUGIN = Path(__file__).parents[1] / "nepoforge/data/plugins/runtime-guard.ts"


class RuntimePluginContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PLUGIN.read_text(encoding="utf-8")

    def test_v242_plugin_setup_and_single_object_hooks(self):
        self.assertIn('import type { Plugin as PluginTypes } from "@opencode/plugin"', self.source)
        self.assertIn("export const RuntimeGuardPlugin: PluginTypes.Plugin", self.source)
        self.assertIn('session.hook("retry", async (input)', self.source)
        self.assertIn('tool.hook("execute.after", async (input)', self.source)
        self.assertIn("retry.decision = { retry: false }", self.source)
        self.assertIn("result.status !== \"error\"", self.source)
        self.assertNotIn('"session.retry":', self.source)
        self.assertNotIn('"tool.execute.after":', self.source)
        self.assertIn('tool.hook("execute.before", async (input)', self.source)
        self.assertIn('import { Error as ToolError } from "@opencode/plugin/promise/tool"', self.source)
        self.assertIn("throw new ToolError(", self.source)
        self.assertNotIn('"@opencode/plugin/tool"', self.source)
        self.assertIn('import type { Plugin as PluginTypes } from "@opencode/plugin"', self.source)

    def test_generated_session_method_arguments_and_wrapping(self):
        self.assertIn("session.get({ sessionID: root })", self.source)
        self.assertIn("session.get({ sessionID: id })", self.source)
        self.assertIn("session.interrupt({ sessionID: id, resume: false })", self.source)
        self.assertIn("response.interrupted", self.source)
        self.assertNotIn("path: { id", self.source)
        self.assertNotIn("client.session.messages", self.source)
        self.assertNotIn("client.message.list", self.source)
        # Plugin Context doesn't expose the root generated client/message domain.
        self.assertIn("no message.list method", self.source)

    def test_event_monitor_scope_accumulation_and_unknown_currency(self):
        self.assertIn("event.subscribe()", self.source)
        self.assertIn('evt.type === "session.usage.updated"', self.source)
        self.assertIn("sessionUsage.set(sessionID", self.source)
        self.assertIn("stepUsage.set(`${sessionID}:${payload.assistantMessageID}`", self.source)
        self.assertIn("if (cumulative) {", self.source)
        self.assertIn("costUSD += cumulative.costUSD", self.source)
        self.assertIn("NEPOFORGE_RUNTIME_GUARD_MAX_COST_USD", self.source)
        self.assertIn("Money.USD", self.source)
        self.assertIn("not linked to Nepoforge's yen budget", self.source)
        self.assertIn("recovery for missed events are absent", self.source)

    def test_scoped_failures_interrupt_and_lifecycle_are_declared_api(self):
        self.assertIn("handledFailures.has(identity)", self.source)
        self.assertIn("`${sessionID}:${messageID}:${callID}`", self.source)
        self.assertIn("const targets = [...sessions.keys()].filter(inScope)", self.source)
        self.assertIn("registration.dispose()", self.source)
        self.assertNotIn("experimental.session.compacting", self.source)
        self.assertIn("not a gate for all new model requests", self.source)
        self.assertIn("cannot protect if unloaded", self.source)

    def test_interrupt_fanout_dispatches_all_before_settling(self):
        latch = self.source.split("const latch = async () => {", 1)[1].split(
            "\n    const track =", 1
        )[0]
        interrupt_one = self.source.split("const interruptOne = async (id: string) => {", 1)[1].split(
            "\n    const latch =", 1
        )[0]
        self.assertIn("const targets = [...sessions.keys()].filter(inScope)", latch)
        self.assertIn("const requests = targets.map((id) => interruptOne(id))", latch)
        self.assertIn("await Promise.allSettled(requests)", latch)
        self.assertNotIn("await interruptOne(id)", latch)
        self.assertIn("await session.interrupt({ sessionID: id, resume: false })", interrupt_one)
        self.assertIn("} catch {", interrupt_one)
        self.assertIn('status: "pending"', self.source)
        self.assertIn('status: "settled"', self.source)
        self.assertIn('status: "error"', self.source)
        self.assertLess(
            latch.index("const requests = targets.map((id) => interruptOne(id))"),
            latch.index("await Promise.allSettled(requests)"),
        )

    def test_default_export_and_module_shape(self):
        self.assertIn("export const RuntimeGuardPlugin:", self.source)
        self.assertIn('id: "nepoforge-runtime-guard"', self.source)
        self.assertIn("setup: async ({ session, tool, event })", self.source)
        self.assertIn("export default RuntimeGuardPlugin", self.source)


if __name__ == "__main__":
    unittest.main()
