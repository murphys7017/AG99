"""Build a semantic prompt tree from a target-projected context pack."""

from __future__ import annotations

from collections.abc import Callable

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest
from astrbot.core.star.context import Context

from ..context_types import ContextPack, ContextSlot
from .interfaces import BasePromptRenderer
from .prompt_tree import NodeRef, PromptBuilder


class PromptTreeBuilder:
    """Translate canonical context slots into the provider-neutral prompt tree."""

    def build(
        self,
        pack: ContextPack,
        *,
        layout: BasePromptRenderer,
        event: AstrMessageEvent | None = None,
        plugin_context: Context | None = None,
        config=None,
        provider_request: ProviderRequest | None = None,
    ) -> PromptBuilder:
        root_tag = layout.get_root_tag()
        prompt_tree = PromptBuilder(root_tag)
        path_refs: dict[str, NodeRef] = {root_tag: prompt_tree.ref()}
        grouped_slots = self._group_slots(pack)
        enabled_groups = [
            group
            for group in layout.get_enabled_slot_groups()
            if group in grouped_slots
        ]
        node_structure = layout.get_node_structure()
        rendered_slots: list[str] = []
        rendered_groups: list[str] = []

        def resolve_node(path: str) -> NodeRef:
            return self._ensure_node_path(
                prompt_tree,
                path_refs=path_refs,
                root_tag=root_tag,
                node_path=path,
            )

        for group in enabled_groups:
            node_path = node_structure.get(group)
            if not node_path:
                continue
            target_ref = resolve_node(node_path)
            rendered = self._build_group(
                layout,
                group=group,
                target=target_ref,
                resolve_node=resolve_node,
                slots=grouped_slots[group],
                pack=pack,
                event=event,
                plugin_context=plugin_context,
                config=config,
                provider_request=provider_request,
            )
            if rendered:
                rendered_groups.append(group)
                rendered_slots.extend(rendered)

        prompt_tree._root_node.meta.update(
            {
                "rendered_slots": rendered_slots,
                "rendered_groups": rendered_groups,
                "layout": layout.get_name(),
                "enabled_slot_groups": list(enabled_groups),
            }
        )
        if "output_contract" in pack.meta:
            prompt_tree._root_node.meta["output_contract"] = pack.meta[
                "output_contract"
            ]
        return prompt_tree

    @staticmethod
    def _build_group(
        layout: BasePromptRenderer,
        *,
        group: str,
        target: NodeRef,
        resolve_node: Callable[[str], NodeRef],
        slots: list[ContextSlot],
        pack: ContextPack,
        event: AstrMessageEvent | None,
        plugin_context: Context | None,
        config,
        provider_request: ProviderRequest | None,
    ) -> list[str]:
        build_method = getattr(layout, f"render_{group}_context")
        return build_method(
            target,
            slots,
            pack=pack,
            resolve_node=resolve_node,
            event=event,
            plugin_context=plugin_context,
            config=config,
            provider_request=provider_request,
        )

    @staticmethod
    def _ensure_node_path(
        prompt_tree: PromptBuilder,
        *,
        path_refs: dict[str, NodeRef],
        root_tag: str,
        node_path: str,
    ) -> NodeRef:
        normalized_path = node_path.strip("/")
        if not normalized_path:
            return prompt_tree.ref()

        parts = normalized_path.split("/")
        if parts[0] == root_tag:
            parts = parts[1:]

        current_path = root_tag
        current_ref = path_refs[root_tag]
        for part in parts:
            current_path = f"{current_path}/{part}"
            if current_path not in path_refs:
                path_refs[current_path] = current_ref.tag(
                    part,
                    meta={"node_path": current_path},
                )
            current_ref = path_refs[current_path]
        return current_ref

    @staticmethod
    def _group_slots(pack: ContextPack) -> dict[str, list[ContextSlot]]:
        grouped_slots: dict[str, list[ContextSlot]] = {}
        for slot in pack.slots.values():
            group = slot.name.split(".", 1)[0]
            grouped_slots.setdefault(group, []).append(slot)
        return grouped_slots


__all__ = ["PromptTreeBuilder"]
