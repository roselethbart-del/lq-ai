/**
 * Unit tests for the playbook detail/edit page helpers.
 *
 * Coverage:
 *   - toEditable        → fetched Playbook mapped to the editor's shape
 *   - toUpdateBody      → PATCH body, positions always sent + renumbered
 *   - validateDraft     → the guards the save button relies on
 *   - isBuiltIn         → built-ins are read-only (API returns 403)
 */
import { describe, expect, it } from 'vitest';
import { isBuiltIn, toEditable, toUpdateBody, validateDraft } from '../page-helpers';
import type { Playbook, PlaybookCreate, Position } from '$lib/lq-ai/types';

function makePosition(overrides: Partial<Position> = {}): Position {
	return {
		id: 'pos-1',
		issue: 'Limitation of Liability',
		description: 'Caps liability.',
		standard_language: 'Liability is capped at fees paid.',
		fallback_tiers: [],
		redline_strategy: 'Push for a 12-month cap.',
		severity_if_missing: 'high',
		detection_keywords: ['liability', 'cap'],
		detection_examples: ['Liability is capped'],
		position_order: 0,
		...overrides
	};
}

function makePlaybook(overrides: Partial<Playbook> = {}): Playbook {
	return {
		id: 'pb-1',
		name: 'Generated Subcontract Playbook',
		contract_type: 'Subcontract',
		description: 'From prior agreements.',
		version: '1.0.0',
		created_by: 'user-1',
		created_at: '2026-09-05T00:00:00Z',
		updated_at: '2026-09-05T00:00:00Z',
		positions: [makePosition()],
		...overrides
	};
}

describe('toEditable', () => {
	it('maps a fetched playbook into the editor shape', () => {
		const draft = toEditable(makePlaybook());
		expect(draft.name).toBe('Generated Subcontract Playbook');
		expect(draft.contract_type).toBe('Subcontract');
		expect(draft.positions).toHaveLength(1);
		expect(draft.positions?.[0].issue).toBe('Limitation of Liability');
	});

	it('drops server-assigned position ids', () => {
		// PATCH replaces the position list wholesale rather than diffing,
		// so ids carry no meaning on the way back in.
		const draft = toEditable(makePlaybook());
		expect(draft.positions?.[0]).not.toHaveProperty('id');
	});

	it('tolerates a playbook with no positions', () => {
		const draft = toEditable(makePlaybook({ positions: [] }));
		expect(draft.positions).toEqual([]);
	});
});

describe('toUpdateBody', () => {
	it('always sends positions so deletions are not silently dropped', () => {
		const draft: PlaybookCreate = {
			name: 'P',
			contract_type: 'Subcontract',
			positions: []
		};
		expect(toUpdateBody(draft).positions).toEqual([]);
	});

	it('renumbers position_order densely from zero', () => {
		// Deleting a middle row otherwise leaves a gap, which the schema
		// and the M3-A2 executor both assume cannot happen.
		const draft: PlaybookCreate = {
			name: 'P',
			contract_type: 'Subcontract',
			positions: [
				{ issue: 'A', standard_language: 'a', severity_if_missing: 'low', position_order: 0 },
				{ issue: 'B', standard_language: 'b', severity_if_missing: 'low', position_order: 7 },
				{ issue: 'C', standard_language: 'c', severity_if_missing: 'low', position_order: 9 }
			]
		};
		expect(toUpdateBody(draft).positions?.map((p) => p.position_order)).toEqual([0, 1, 2]);
	});
});

describe('validateDraft', () => {
	const base: PlaybookCreate = {
		name: 'P',
		contract_type: 'Subcontract',
		positions: [{ issue: 'A', standard_language: 'a', severity_if_missing: 'low' }]
	};

	it('accepts a complete draft', () => {
		expect(validateDraft(base)).toBeNull();
	});

	it('rejects a blank name', () => {
		expect(validateDraft({ ...base, name: '   ' })).toMatch(/name is required/i);
	});

	it('rejects a blank contract type', () => {
		expect(validateDraft({ ...base, contract_type: '' })).toMatch(/contract type is required/i);
	});

	it('names the offending position when an issue label is blank', () => {
		const draft: PlaybookCreate = {
			...base,
			positions: [
				{ issue: 'A', standard_language: 'a', severity_if_missing: 'low' },
				{ issue: '  ', standard_language: 'b', severity_if_missing: 'low' }
			]
		};
		expect(validateDraft(draft)).toMatch(/Position 2/);
	});

	it('names the offending position when standard language is blank', () => {
		const draft: PlaybookCreate = {
			...base,
			positions: [{ issue: 'A', standard_language: '', severity_if_missing: 'low' }]
		};
		expect(validateDraft(draft)).toMatch(/Position 1 needs standard language/);
	});
});

describe('isBuiltIn', () => {
	it('treats a null created_by as built-in', () => {
		// Seed-migration playbooks; the API 403s on edit and delete.
		expect(isBuiltIn(makePlaybook({ created_by: null }))).toBe(true);
	});

	it('treats an owned playbook as editable', () => {
		expect(isBuiltIn(makePlaybook({ created_by: 'user-1' }))).toBe(false);
	});
});
