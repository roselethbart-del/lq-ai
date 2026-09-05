/**
 * Pure helpers for the playbook detail/edit page (`/lq-ai/playbooks/[id]`).
 *
 * Kept in a sibling `.ts` so vitest can exercise them without the svelte
 * transformer, matching the `playbooks/page-helpers.ts` and
 * `playbooks/easy/page-helpers.ts` pattern.
 */
import type {
	Playbook,
	PlaybookCreate,
	PlaybookUpdate,
	Position,
	PositionCreate
} from '$lib/lq-ai/types';

/**
 * Strip a saved `Position` down to the `PositionCreate` shape the editor
 * binds to.
 *
 * `id` is dropped deliberately: `PATCH /playbooks/{id}` replaces the
 * position list wholesale rather than diffing it, so server-assigned ids
 * are meaningless on the way back in.
 */
export function toPositionCreate(position: Position): PositionCreate {
	return {
		issue: position.issue,
		description: position.description,
		standard_language: position.standard_language,
		fallback_tiers: position.fallback_tiers,
		redline_strategy: position.redline_strategy,
		severity_if_missing: position.severity_if_missing,
		detection_keywords: position.detection_keywords,
		detection_examples: position.detection_examples,
		position_order: position.position_order
	};
}

/**
 * Convert a fetched `Playbook` into the `PlaybookCreate` the shared
 * `PlaybookEditor` component edits — the same shape the Easy wizard's
 * Step 3 binds, so one editor serves both surfaces.
 */
export function toEditable(playbook: Playbook): PlaybookCreate {
	return {
		name: playbook.name,
		contract_type: playbook.contract_type,
		description: playbook.description,
		version: playbook.version,
		positions: (playbook.positions ?? []).map(toPositionCreate)
	};
}

/**
 * Build the PATCH body from the edited draft.
 *
 * `positions` is always sent: the editor is the whole-playbook surface,
 * so omitting it would silently discard deletions the user just made.
 * Renumbered densely because the schema and the M3-A2 executor both
 * assume `position_order` is dense and zero-indexed — deleting a middle
 * row otherwise leaves a gap.
 */
export function toUpdateBody(draft: PlaybookCreate): PlaybookUpdate {
	return {
		name: draft.name,
		contract_type: draft.contract_type,
		description: draft.description ?? '',
		version: draft.version,
		positions: (draft.positions ?? []).map((p, index) => ({
			...p,
			position_order: index
		}))
	};
}

/**
 * Operator-facing validation. Returns an error string, or `null` when the
 * draft is safe to save.
 *
 * Mirrors the wizard's save-button guard so both surfaces refuse the same
 * inputs, rather than one relying on a 422 round-trip.
 */
export function validateDraft(draft: PlaybookCreate): string | null {
	if (!draft.name?.trim()) {
		return 'Playbook name is required.';
	}
	if (!draft.contract_type?.trim()) {
		return 'Contract type is required.';
	}
	const positions = draft.positions ?? [];
	const blankIssue = positions.findIndex((p) => !p.issue?.trim());
	if (blankIssue !== -1) {
		return `Position ${blankIssue + 1} needs an issue label.`;
	}
	const blankLanguage = positions.findIndex((p) => !p.standard_language?.trim());
	if (blankLanguage !== -1) {
		return `Position ${blankLanguage + 1} needs standard language.`;
	}
	return null;
}

/**
 * Built-in playbooks ship via seed migration and carry `created_by: null`.
 * The API refuses to edit or delete them (403), so the UI disables those
 * affordances rather than offering an action that cannot succeed.
 */
export function isBuiltIn(playbook: Playbook): boolean {
	return playbook.created_by === null;
}
