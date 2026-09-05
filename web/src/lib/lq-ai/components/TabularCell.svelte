<script context="module" lang="ts">
	// Aliased: the instance script below imports the same type under its
	// own name, and module-script declarations share a scope with it.
	import type { TabularCellResult as TabularCellResultModule } from '$lib/lq-ai/types';

	/**
	 * Copy for a failed cell, and whether it carries an explanation.
	 *
	 * Exported (and tested) separately from the component because the
	 * wording is a substantive claim about the document, not styling.
	 * "not found" tells a reviewer the document is silent on the column
	 * — something they may act on. We are only entitled to say that when
	 * retrieval actually put relevant text in front of the model.
	 */
	export function describeFailedCell(cell: TabularCellResultModule | undefined): {
		label: string;
		title: string | undefined;
	} {
		if (cell?.retrieval === 'fallback') {
			return {
				label: 'not located',
				title:
					'Search found no text relevant to this column in this document, so nothing was read for it. This is not a finding that the document is silent.'
			};
		}
		if (cell?.retrieval === 'empty') {
			return {
				label: 'no text',
				title: 'This document has no extracted text to search.'
			};
		}
		return { label: 'not found', title: undefined };
	}
</script>

<script lang="ts">
	/**
	 * Single tabular cell — M3-C3 sub-phase 4.
	 *
	 * Renders the cell value + a confidence chip (right-aligned). Click
	 * dispatches `open` so the result-view parent can pop the citation
	 * modal for this cell (Decision C-2: hybrid chip + click surface).
	 * Failed cells (Decision C-10) render italic "not found" + amber
	 * chip; visually distinct from the Citation Engine's red unverified.
	 *
	 * A failed cell whose `retrieval` is `fallback` renders "not located"
	 * instead of "not found". The distinction is the whole point: "not
	 * found" asserts the document is silent on the column, which a
	 * reviewer may rely on. When search never surfaced anything relevant,
	 * the model only ever saw the document's opening pages, so the cell
	 * must not make that assertion.
	 */
	import { createEventDispatcher } from 'svelte';

	import type { TabularCellConfidence, TabularCellResult } from '$lib/lq-ai/types';

	type CellRenderState = 'empty' | 'failed' | 'high' | 'medium' | 'low';

	function cellRenderState(c: TabularCellResult | undefined): CellRenderState {
		if (c === undefined) return 'empty';
		return c.confidence;
	}

	function confidenceChipLabel(confidence: TabularCellConfidence): string {
		switch (confidence) {
			case 'high':
				return 'High';
			case 'medium':
				return 'Med';
			case 'low':
				return 'Low';
			case 'failed':
				return 'Failed';
		}
	}

	export let cell: TabularCellResult | undefined;
	/**
	 * Optional metadata — only used for the dispatched `open` event so
	 * the parent's citation modal can show a header like
	 * `<column> — <document>`.
	 */
	export let documentName: string = '';
	export let columnName: string = '';

	const dispatch = createEventDispatcher<{ open: { documentName: string; columnName: string } }>();

	$: state = cellRenderState(cell) as CellRenderState;
	$: clickable = state !== 'empty';
	$: failed = describeFailedCell(cell);

	function handleClick(): void {
		if (!clickable) return;
		dispatch('open', { documentName, columnName });
	}

	function handleKey(e: KeyboardEvent): void {
		if (!clickable) return;
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			handleClick();
		}
	}
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<!-- role + tabindex are gated together on `clickable`; the static
     analyzer flags the dynamic tabindex without seeing the role guard. -->
<div
	class="lq-tabcell"
	data-state={state}
	data-testid="lq-tabcell"
	data-document-name={documentName}
	data-column-name={columnName}
	role={clickable ? 'button' : undefined}
	tabindex={clickable ? 0 : undefined}
	on:click={handleClick}
	on:keydown={handleKey}
>
	{#if state === 'empty'}
		<span class="lq-tabcell__placeholder" aria-label="not yet computed">…</span>
	{:else if state === 'failed'}
		<span
			class="lq-tabcell__failed"
			class:lq-tabcell__failed--explained={failed.title !== undefined}
			data-testid="lq-tabcell-failed"
			data-retrieval={cell?.retrieval ?? undefined}
			title={failed.title}
		>
			{failed.label}
		</span>
	{:else}
		<span class="lq-tabcell__value" data-testid="lq-tabcell-value">{cell?.value ?? ''}</span>
	{/if}

	{#if cell && state !== 'empty'}
		<span
			class="lq-tabcell__chip"
			data-confidence={cell.confidence}
			data-testid="lq-tabcell-chip"
			aria-label={`Confidence: ${confidenceChipLabel(cell.confidence)}`}
		>
			{confidenceChipLabel(cell.confidence)}
		</span>
	{/if}
</div>

<style>
	.lq-tabcell {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
		padding: 0.5rem 0.625rem;
		min-height: 2.5rem;
		font-size: 0.875rem;
		color: var(--lq-text);
		background: var(--lq-surface);
		border: 1px solid transparent;
		cursor: default;
	}
	.lq-tabcell[role='button'] {
		cursor: pointer;
	}
	.lq-tabcell[role='button']:hover {
		background: var(--lq-inset);
	}
	.lq-tabcell[role='button']:focus-visible {
		outline: 2px solid var(--lq-accent, #4f46e5);
		outline-offset: -2px;
	}
	.lq-tabcell__placeholder {
		color: var(--lq-text-secondary);
	}
	.lq-tabcell__value {
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.lq-tabcell__failed {
		flex: 1;
		font-style: italic;
		color: var(--lq-text-secondary);
	}
	/* Dotted underline marks the cell as carrying an explanation, so a
	   reviewer scanning the grid can see which blanks are assertions
	   about the document and which are gaps in what we read. */
	.lq-tabcell__failed--explained {
		text-decoration: underline dotted;
		text-underline-offset: 0.2em;
		cursor: help;
	}
	.lq-tabcell__chip {
		flex-shrink: 0;
		display: inline-block;
		padding: 0.0625rem 0.375rem;
		border-radius: 999px;
		font-size: 0.6875rem;
		font-weight: 600;
		letter-spacing: 0.02em;
		text-transform: uppercase;
	}
	.lq-tabcell__chip[data-confidence='high'] {
		background: var(--lq-success-soft, #dcfce7);
		color: var(--lq-success, #166534);
	}
	.lq-tabcell__chip[data-confidence='medium'] {
		background: var(--lq-inset, #e5e7eb);
		color: var(--lq-text, #1f2937);
	}
	.lq-tabcell__chip[data-confidence='low'] {
		background: var(--lq-warning-soft, #fef3c7);
		color: var(--lq-warning, #92400e);
	}
	.lq-tabcell__chip[data-confidence='failed'] {
		/* Amber per Decision C-10 — distinct from Citation Engine's red
		   unverified state. */
		background: var(--lq-warning-soft, #fef3c7);
		color: var(--lq-warning, #92400e);
		border: 1px solid var(--lq-warning, #92400e);
	}
</style>
