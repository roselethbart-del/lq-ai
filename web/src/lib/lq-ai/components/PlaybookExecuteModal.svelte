<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { goto } from '$app/navigation';

	import { executePlaybook } from '$lib/lq-ai/api/playbooks';
	import { LQAIApiError } from '$lib/lq-ai/api/client';
	import {
		estimatePlaybookCost,
		formatCostUSD,
		DEFAULT_JUDGE_MODEL
	} from '$lib/lq-ai/playbookCost';
	import { listFiles } from '$lib/lq-ai/api/files';
	import type { Playbook, FileMeta } from '$lib/lq-ai/types';

	export let playbook: Playbook;

	const dispatch = createEventDispatcher<{ close: void }>();

	// The caller's own documents. Previously this picker was knowledge-base
	// scoped: pick a KB, then a file within it. But `POST /playbooks/{id}/execute`
	// only ever needed a `target_document_id`, and the executor never touches
	// knowledge bases at all — the KB step was scaffolding to reach a document
	// id, and it made a contract un-reviewable until it had been filed into a
	// precedent library. A counterparty's draft usually has no business being
	// there, so the picker now lists documents directly.
	let files: FileMeta[] = [];
	let filesLoading = false;
	let filesError: string | null = null;
	let selectedFileId = '';
	let filterText = '';

	let executing = false;
	let executeError: string | null = null;

	// Client-side cost preview per §5.2 decision.
	$: cost = estimatePlaybookCost(playbook, DEFAULT_JUDGE_MODEL);

	// `parsed_only` already excludes unparsed rows server-side; this keeps the
	// type narrowing honest rather than trusting the query param.
	$: eligibleFiles = files.filter(
		(f): f is FileMeta & { document_id: string } =>
			typeof f.document_id === 'string' && f.document_id.length > 0
	);

	// Free-text filter — 38 contracts is already past comfortable scrolling.
	$: visibleFiles = filterText.trim()
		? eligibleFiles.filter((f) =>
				f.filename.toLowerCase().includes(filterText.trim().toLowerCase())
			)
		: eligibleFiles;

	$: selectedFile = eligibleFiles.find((f) => f.id === selectedFileId) ?? null;
	$: selectedDocumentId = selectedFile?.document_id ?? '';

	async function loadFiles(): Promise<void> {
		filesLoading = true;
		filesError = null;
		try {
			files = await listFiles({ parsedOnly: true, limit: 500 });
		} catch (err) {
			filesError = err instanceof LQAIApiError ? err.message : 'Failed to load documents.';
			files = [];
		} finally {
			filesLoading = false;
		}
	}

	async function handleExecute(): Promise<void> {
		if (!selectedDocumentId) return;
		executing = true;
		executeError = null;
		try {
			const exec = await executePlaybook(playbook.id, {
				target_document_id: selectedDocumentId
			});
			dispatch('close');
			await goto(`/lq-ai/playbook-executions/${exec.id}`);
		} catch (err) {
			executeError =
				err instanceof LQAIApiError ? err.message : 'Failed to start playbook execution.';
			executing = false;
		}
	}

	function handleOverlayClick(): void {
		if (!executing) {
			dispatch('close');
		}
	}

	function handleCancel(): void {
		dispatch('close');
	}

	void loadFiles();
</script>

<div class="lq-modal-overlay" on:click={handleOverlayClick} role="presentation"></div>

<div
	class="lq-modal"
	role="dialog"
	aria-modal="true"
	aria-labelledby="lq-execute-title"
	data-testid="lq-playbook-execute-modal"
>
	<header class="lq-modal__header">
		<h2 id="lq-execute-title">Apply playbook: {playbook.name}</h2>
		<button
			type="button"
			class="lq-modal__close"
			on:click={handleCancel}
			disabled={executing}
			aria-label="Close"
		>
			×
		</button>
	</header>

	<div class="lq-modal__body">
		<div class="lq-modal__field">
			<label for="lq-execute-doc">Target document</label>
			{#if filesLoading}
				<div class="lq-modal__placeholder">Loading documents…</div>
			{:else if filesError}
				<div class="lq-modal__placeholder" role="alert">{filesError}</div>
			{:else if eligibleFiles.length === 0}
				<div class="lq-modal__placeholder">
					No parsed documents yet. Upload a contract and wait for it to finish
					processing, then apply the playbook to it.
				</div>
			{:else}
				{#if eligibleFiles.length > 8}
					<input
						type="search"
						class="lq-modal__filter"
						placeholder="Filter by filename…"
						bind:value={filterText}
						data-testid="lq-playbook-execute-doc-filter"
						disabled={executing}
					/>
				{/if}
				<select
					id="lq-execute-doc"
					bind:value={selectedFileId}
					data-testid="lq-playbook-execute-doc-picker"
					disabled={executing}
				>
					<option value="">Choose a document…</option>
					{#each visibleFiles as f (f.id)}
						<option value={f.id}>{f.filename}</option>
					{/each}
				</select>
				{#if filterText.trim() && visibleFiles.length === 0}
					<div class="lq-modal__placeholder">No documents match “{filterText}”.</div>
				{/if}
			{/if}
		</div>

		<div class="lq-modal__cost" data-testid="lq-playbook-cost-preview">
			<div class="lq-modal__cost-label">Estimated cost</div>
			<div class="lq-modal__cost-amount">{formatCostUSD(cost.estimated_cost_usd)}</div>
			<div class="lq-modal__cost-detail">
				{cost.position_count} position{cost.position_count === 1 ? '' : 's'} · model: {cost.judge_model}
			</div>
		</div>

		{#if executeError}
			<div class="lq-modal__error" role="alert" data-testid="lq-playbook-execute-error">
				{executeError}
			</div>
		{/if}
	</div>

	<footer class="lq-modal__footer">
		<button
			type="button"
			class="lq-modal__btn lq-modal__btn--secondary"
			on:click={handleCancel}
			disabled={executing}
		>
			Cancel
		</button>
		<button
			type="button"
			class="lq-modal__btn lq-modal__btn--primary"
			on:click={handleExecute}
			disabled={!selectedDocumentId || executing}
			data-testid="lq-playbook-execute-confirm"
		>
			{executing ? 'Starting…' : 'Run playbook'}
		</button>
	</footer>
</div>

<style>
	.lq-modal-overlay {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.4);
		z-index: 1000;
	}
	.lq-modal {
		position: fixed;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		width: min(90vw, 32rem);
		max-height: 90vh;
		overflow-y: auto;
		background: var(--lq-surface, #ffffff);
		border: 1px solid var(--lq-border, #e5e7eb);
		border-radius: 0.5rem;
		z-index: 1001;
		display: flex;
		flex-direction: column;
		color: var(--lq-text-primary, #111827);
	}
	.lq-modal__header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 1rem 1.25rem;
		border-bottom: 1px solid var(--lq-border, #e5e7eb);
	}
	.lq-modal__header h2 {
		margin: 0;
		font-size: 1.125rem;
	}
	.lq-modal__close {
		background: none;
		border: none;
		font-size: 1.5rem;
		line-height: 1;
		cursor: pointer;
		color: var(--lq-text-secondary, #6b7280);
		padding: 0 0.25rem;
	}
	.lq-modal__close:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
	.lq-modal__body {
		padding: 1.25rem;
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.lq-modal__field label {
		display: block;
		font-size: 0.875rem;
		font-weight: 500;
		margin-bottom: 0.375rem;
	}
	.lq-modal__field select {
		width: 100%;
		padding: 0.5rem 0.625rem;
		border: 1px solid var(--lq-border, #e5e7eb);
		border-radius: 0.375rem;
		background: var(--lq-surface, #ffffff);
		color: var(--lq-text-primary, #111827);
		font-size: 0.9375rem;
	}
	.lq-modal__field select:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.lq-modal__filter {
		width: 100%;
		padding: 0.5rem 0.625rem;
		margin-bottom: 0.5rem;
		border: 1px solid var(--lq-border, #e5e7eb);
		border-radius: 0.375rem;
		background: var(--lq-surface, #ffffff);
		color: var(--lq-text-primary, #111827);
		font-size: 0.9375rem;
	}
	.lq-modal__filter:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
	.lq-modal__placeholder {
		padding: 0.5rem 0.625rem;
		background: var(--lq-inset, #f3f4f6);
		border-radius: 0.375rem;
		font-size: 0.875rem;
		color: var(--lq-text-secondary, #6b7280);
	}
	.lq-modal__cost {
		padding: 0.875rem 1rem;
		background: var(--lq-inset, #f3f4f6);
		border-radius: 0.5rem;
	}
	.lq-modal__cost-label {
		font-size: 0.8125rem;
		color: var(--lq-text-secondary, #6b7280);
	}
	.lq-modal__cost-amount {
		font-size: 1.5rem;
		font-weight: 600;
		margin: 0.125rem 0;
	}
	.lq-modal__cost-detail {
		font-size: 0.8125rem;
		color: var(--lq-text-tertiary, var(--lq-text-secondary, #6b7280));
	}
	.lq-modal__error {
		padding: 0.625rem 0.875rem;
		background: var(--lq-error-soft, var(--lq-inset, #fef2f2));
		border: 1px solid var(--lq-error-border, var(--lq-border, #fecaca));
		color: var(--lq-error, #b91c1c);
		border-radius: 0.375rem;
		font-size: 0.875rem;
	}
	.lq-modal__footer {
		display: flex;
		justify-content: flex-end;
		gap: 0.5rem;
		padding: 1rem 1.25rem;
		border-top: 1px solid var(--lq-border, #e5e7eb);
	}
	.lq-modal__btn {
		padding: 0.5rem 1rem;
		border-radius: 0.375rem;
		font-size: 0.875rem;
		cursor: pointer;
		border: 1px solid transparent;
	}
	.lq-modal__btn--secondary {
		background: var(--lq-surface, #ffffff);
		border-color: var(--lq-border, #e5e7eb);
		color: var(--lq-text-primary, #111827);
	}
	.lq-modal__btn--primary {
		background: var(--lq-accent, #4f46e5);
		color: var(--lq-on-accent, #ffffff);
	}
	.lq-modal__btn--primary:disabled,
	.lq-modal__btn--secondary:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}
</style>
