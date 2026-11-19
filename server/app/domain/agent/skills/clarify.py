"""확인 질문을 하거나 거절을 받아들이는 대화 스킬."""

from app.core.i18n import Translator
from app.domain.agent.clarify import ClarifyStore
from app.domain.agent.context import TurnContext
from app.domain.agent.model import Clarify, Dismiss, Intent
from app.domain.agent.responses import build_response, render
from app.domain.agent.skills.base import KEY_INTERNAL, BaseSkill, SkillResult

KEY_CANCELLED = "agent.clarify.cancelled"


class ClarifySkill(BaseSkill):
    """Clarify 는 질문을 허브에 보류해 두고 되묻는다. Dismiss 는 거절 답을 받는다."""

    intents = (Clarify, Dismiss)

    def __init__(self, translate: Translator, store: ClarifyStore) -> None:
        super().__init__(translate)
        self._store = store

    async def execute(self, intent: Intent, ctx: TurnContext) -> SkillResult:
        if isinstance(intent, Dismiss):
            return SkillResult(True, KEY_CANCELLED, text=self._t(KEY_CANCELLED))
        if not isinstance(intent, Clarify):
            return self.failed(KEY_INTERNAL)
        self._store.remember(ctx.conversation_key, intent)
        response = build_response(intent, ctx.view.model, ctx.now, self._t)
        if response is None:
            return self.failed(KEY_INTERNAL)
        return SkillResult(
            True,
            response.key,
            response.args,
            intent.candidate_entity_ids,
            render(response, self._t),
        )
