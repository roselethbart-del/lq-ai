<script lang="ts">
	/**
	 * Playbook detail / edit page.
	 *
	 * The API has exposed GET / PATCH / DELETE for a single playbook since
	 * M3-A6, and `PlaybookEditor` has existed since the Easy wizard, but
	 * nothing wired the two together — a saved playbook could be listed and
	 * applied, never reviewed or corrected. That gap matters most right
	 * after an Easy generation, whose output is explicitly a starting point
	 * the user-attorney is expected to edit.
	 *
	 * This page binds the same editor the wizard's Step 3 uses, so both
	 * surfaces present one editing experience.
	 */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { toast } from 'svelte-sonner';

	import PlaybookEditor from '$lib/lq-ai/components/PlaybookEditor.svelte';
	import { deletePlaybook, getPlaybook, updatePlaybook } from '$lib/lq-ai/api/playbooks';
	import { LQAIApiError } from '$lib/lq-ai/api/client';
	import type { Playbook, PlaybookCreate } from '$lib/lq-ai/types';
	import { isBuiltIn, toEditable, toUpdateBody, validateDraft } from './page-helpers';

	let playbook: Playbook | null = null;
	let draft: PlaybookCreate | null = null;
	let loading = true;
	let saving = false;
	let deleting = false;
	let loadError: string | null = null;
	let saveError: string | null = null;

	// `$page.params.id` is `string | undefined` in SvelteKit's generated
	// types; the route can't match without it, so an empty fallback keeps
	// the call sites typed without inventing a runtime case.
	$: playbookId = $page.params.id ?? '';
	$: readOnly = playbook !== null && isBuiltIn(playbook);
	$: positionCount = draft?.positions?.length ?? 0;

	onMount(async () => {
		try {
			const fetched = await getPlaybook(playbookId);
			playbook = fetched;
			draft = toEditable(fetched);
		} catch (err) {
			loadError =
				err instanceof LQAIApiError ? err.message : 'Failed to load this playbook.';
		} finally {
			loading = false;
		}
	});

	async function handleSave(): Promise<void> {
		if (!draft || readOnly) return;
		const invalid = validateDraft(draft);
		if (invalid) {
			saveError = invalid;
			return;
		}
		saveError = null;
		saving = true;
		try {
			const updated = await updatePlaybook(playbookId, toUpdateBody(draft));
			playbook = updated;
			draft = toEditable(updated);
			toast.success('Playbook saved.');
		} catch (err) {
			const message = err instanceof LQAIApiError ? err.message : 'Failed to save the playbook.';
			saveError = message;
			toast.error(message);
		} finally {
			saving = false;
		}
	}

	async function handleDelete(): Promise<void> {
		if (!playbook || readOnly) return;
		// Native confirm(), matching ChatSidebarRow / SavedPromptsPanel.
		if (!confirm(`Delete "${playbook.name}"? This cannot be undone from the UI.`)) {
			return;
		}
		deleting = true;
		try {
			await deletePlaybook(playbookId);
			toast.success('Playbook deleted.');
			await goto('/lq-ai/playbooks');
		} catch (err) {
			toast.error(err instanceof LQAIApiError ? err.message : 'Failed to delete the playbook.');
			deleting = false;
		}
	}
</script>

<section class="lq-playbook-detail">
	<header class="lq-playbook-detail__header">
		<button
			type="button"
			class="lq-playbook-detail__back"
			on:click={() => goto('/lq-ai/playbooks')}
			data-testid="lq-playbook-detail-back"
		>
			← Playbooks
		</button>

		{#if playbook}
			<div class="lq-playbook-detail__actions">
				{#if !readOnly}
					<button
						type="button"
						class="lq-playbook-detail__btn lq-playbook-detail__btn--danger"
						on:click={handleDelete}
						disabled={saving || deleting}
						data-testid="lq-playbook-detail-delete"
					>
						{deleting ? 'Deleting…' : 'Delete'}
					</button>
					<button
						type="button"
						class="lq-playbook-detail__btn lq-playbook-detail__btn--primary"
						on:click={handleSave}
						disabled={saving || deleting}
						data-testid="lq-playbook-detail-save"
					>
						{saving ? 'Saving…' : 'Save changes'}
					</button>
				{/if}
			</div>
		{/if}
	</header>

	{#if loading}
		<div class="lq-playbook-detail__state" data-testid="lq-playbook-detail-loading">Loading…</div>
	{:else if loadError}
		<div class="lq-playbook-detail__error" role="alert" data-testid="lq-playbook-detail-error">
			{loadError}
		</div>
	{:else if draft && playbook}
		<div class="lq-playbook-detail__meta">
			<strong>{playbook.name}</strong>
			<span>{positionCount} position{positionCount === 1 ? '' : 's'}</span>
		</div>

		{#if readOnly}
			<div class="lq-playbook-detail__hint" data-testid="lq-playbook-detail-builtin">
				This is a built-in playbook. Built-ins ship with the product and can't be edited in
				place — apply it as-is, or use it as the basis for your own copy.
			</div>
		{/if}

		{#if saveError}
			<div class="lq-playbook-detail__error" role="alert">{saveError}</div>
		{/if}

		<PlaybookEditor bind:playbook={draft} disabled={readOnly || saving || deleting} />
	{/if}
</section>

<style>
	.lq-playbook-detail {
		padding: 16px 20px 40px;
	}
	.lq-playbook-detail__header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		margin-bottom: 12px;
	}
	.lq-playbook-detail__back {
		background: transparent;
		border: 0;
		padding: 4px 0;
		font-size: 14px;
		color: var(--lq-text-secondary);
		cursor: pointer;
	}
	.lq-playbook-detail__back:hover {
		color: var(--lq-accent);
	}
	.lq-playbook-detail__actions {
		display: flex;
		gap: 8px;
	}
	.lq-playbook-detail__btn {
		border-radius: 4px;
		border: 1px solid var(--lq-border);
		background: transparent;
		color: var(--lq-text);
		padding: 6px 14px;
		font-size: 13px;
		cursor: pointer;
	}
	.lq-playbook-detail__btn:disabled {
		opacity: 0.6;
		cursor: default;
	}
	.lq-playbook-detail__btn--primary {
		background: var(--lq-accent);
		border-color: var(--lq-accent);
		color: #fff;
	}
	.lq-playbook-detail__btn--danger:hover:not(:disabled) {
		border-color: #b42318;
		color: #b42318;
	}
	.lq-playbook-detail__meta {
		display: flex;
		align-items: baseline;
		gap: 10px;
		margin-bottom: 12px;
		font-size: 14px;
	}
	.lq-playbook-detail__meta span {
		color: var(--lq-text-tertiary);
		font-size: 13px;
	}
	.lq-playbook-detail__state,
	.lq-playbook-detail__hint {
		color: var(--lq-text-tertiary);
		font-size: 14px;
		padding: 8px 0;
	}
	.lq-playbook-detail__error {
		color: #b42318;
		font-size: 13px;
		padding: 8px 0;
	}
</style>
