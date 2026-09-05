/**
 * Unit tests for TabularCell's failed-cell copy.
 *
 * Convention note: this codebase does not install @testing-library/svelte
 * (see ChatSidebarRow.test.ts / AttachKBModal.test.ts). The .svelte file
 * exports its pure logic from <script context="module">; we exercise that
 * helper here.
 *
 * Why this wording is tested at all: "not found" in a review grid reads
 * as a finding about the contract — that it is silent on the column, a
 * conclusion a reviewer may act on. The grid is only entitled to say
 * that when retrieval actually put relevant text in front of the model.
 * When search missed and the model was handed the document's opening
 * pages instead, the cell has to say something weaker.
 */
import { describe, expect, it } from 'vitest';
import { describeFailedCell } from '../components/TabularCell.svelte';
import type { TabularCellResult } from '../types';

function makeCell(overrides: Partial<TabularCellResult> = {}): TabularCellResult {
	return {
		value: null,
		citations: [],
		confidence: 'failed',
		error: 'not found in the retrieved text',
		...overrides
	};
}

describe('describeFailedCell', () => {
	it('says "not found" when retrieval matched — the document really is silent', () => {
		const result = describeFailedCell(makeCell({ retrieval: 'matched' }));
		expect(result.label).toBe('not found');
		expect(result.title).toBeUndefined();
	});

	it('says "not located" when retrieval fell back, and explains why', () => {
		const result = describeFailedCell(makeCell({ retrieval: 'fallback' }));
		expect(result.label).toBe('not located');
		expect(result.title).toContain('not a finding that the document is silent');
	});

	it('says "no text" when the document had nothing to search', () => {
		const result = describeFailedCell(makeCell({ retrieval: 'empty' }));
		expect(result.label).toBe('no text');
		expect(result.title).toContain('no extracted text');
	});

	it('falls back to "not found" for cells from before the field existed', () => {
		// Executions predating `retrieval` must keep rendering as they
		// always did rather than showing an empty or misleading label.
		expect(describeFailedCell(makeCell()).label).toBe('not found');
		expect(describeFailedCell(makeCell({ retrieval: null })).label).toBe('not found');
		expect(describeFailedCell(undefined).label).toBe('not found');
	});
});
