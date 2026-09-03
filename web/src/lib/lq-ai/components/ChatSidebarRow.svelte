<script context="module" lang="ts">
	/**
	 * One chat row inside ChatSidebar: select on click, rename via the
	 * hover-revealed pencil icon or a double-click on the title, delete
	 * via the hover-revealed trash icon (backed by the soft-delete
	 * DELETE /chats/{id}, which sets archived_at).
	 *
	 * Pure helpers exported here so vitest can exercise them without
	 * @testing-library/svelte (not installed; see AttachedSkillPill.svelte /
	 * AttachKBModal.test.ts for the established convention).
	 */
	import type { Chat } from '../types';

	/** Mirrors TITLE_MAX_LEN in api/app/schemas/chats.py — the backend
	 * rejects a longer title with a 422; capping client-side avoids a
	 * silent round-trip failure. */
	export const CHAT_TITLE_MAX_LENGTH = 200;

	/** Confirmation copy shown before a delete (native `confirm()`, matching
	 * SavedPromptsPanel.svelte's delete-confirmation convention). */
	export function deleteConfirmMessage(chat: Chat): string {
		return `Delete "${chat.title || 'Untitled chat'}"? This cannot be undone.`;
	}

	/**
	 * Decide whether a rename commit should fire onRename: the trimmed
	 * draft must be non-empty and different from the chat's current title.
	 * Returns the trimmed title to send, or null when no call should be made.
	 */
	export function resolveRenameCommit(draftTitle: string, currentTitle: string): string | null {
		const trimmed = draftTitle.trim();
		if (!trimmed || trimmed === currentTitle) return null;
		return trimmed;
	}
</script>

<script lang="ts">
	import { tick } from 'svelte';

	export let chat: Chat;
	export let active: boolean = false;
	export let onSelect: (chat: Chat) => void = () => undefined;
	export let onRename: (chat: Chat, newTitle: string) => void = () => undefined;
	export let onDelete: (chat: Chat) => void = () => undefined;

	let editing = false;
	let draftTitle = '';
	let inputEl: HTMLInputElement | null = null;

	async function startRename() {
		draftTitle = chat.title || '';
		editing = true;
		// Wait for the input to mount, then focus it.
		await tick();
		inputEl?.focus();
		inputEl?.select();
	}

	function commitRename() {
		if (!editing) return;
		editing = false;
		const newTitle = resolveRenameCommit(draftTitle, chat.title);
		if (newTitle) {
			onRename(chat, newTitle);
		}
	}

	function cancelRename() {
		editing = false;
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			commitRename();
		} else if (e.key === 'Escape') {
			e.preventDefault();
			cancelRename();
		}
	}

	function handleDeleteClick() {
		if (confirm(deleteConfirmMessage(chat))) {
			onDelete(chat);
		}
	}
</script>

<li class="lq-chat-item group">
	{#if editing}
		<input
			bind:this={inputEl}
			bind:value={draftTitle}
			class="lq-chat-row-input"
			on:keydown={handleKeydown}
			on:blur={commitRename}
			maxlength={CHAT_TITLE_MAX_LENGTH}
			data-testid={`lq-ai-chatrow-rename-input-${chat.id}`}
		/>
	{:else}
		<button
			type="button"
			class="lq-chat-row {active ? 'lq-chat-row--active' : ''}"
			on:click={() => onSelect(chat)}
			on:dblclick={(e) => {
				e.preventDefault();
				startRename();
			}}
			data-testid={`lq-ai-chat-${chat.id}`}
		>
			{chat.title || 'Untitled chat'}
		</button>

		<div class="lq-chat-row-actions">
			<button
				type="button"
				class="lq-chat-row-action"
				title="Rename"
				aria-label="Rename chat"
				on:click|stopPropagation={startRename}
				data-testid={`lq-ai-chatrow-rename-${chat.id}`}
			>
				<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.4">
					<path
						d="M11.5 2.5a1.5 1.5 0 0 1 2.12 2.12l-7.6 7.6-2.7.6.6-2.7 7.58-7.62Z"
						stroke-linecap="round"
						stroke-linejoin="round"
					/>
				</svg>
			</button>
			<button
				type="button"
				class="lq-chat-row-action"
				title="Delete"
				aria-label="Delete chat"
				on:click|stopPropagation={handleDeleteClick}
				data-testid={`lq-ai-chatrow-delete-${chat.id}`}
			>
				<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.4">
					<path
						d="M3 4.5h10M6.5 4.5V3a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1.5M6 7v5M10 7v5M4 4.5l.6 8.4a1 1 0 0 0 1 .9h4.8a1 1 0 0 0 1-.9l.6-8.4"
						stroke-linecap="round"
						stroke-linejoin="round"
					/>
				</svg>
			</button>
		</div>
	{/if}
</li>

<style>
	.lq-chat-row {
		display: block;
		width: 100%;
		text-align: left;
		padding: 6px 20px;
		font-size: 14px;
		border-radius: 2px;
		color: var(--lq-text);
		background: transparent;
		border: 0;
		cursor: pointer;
	}
	.lq-chat-row:hover {
		background: var(--lq-accent-soft);
	}
	.lq-chat-row--active {
		background: var(--lq-accent-soft);
		color: var(--lq-accent);
		border-left: 2px solid var(--lq-accent);
		padding-left: 18px;
	}

	.lq-chat-item {
		position: relative;
	}

	.lq-chat-row-actions {
		position: absolute;
		right: 4px;
		top: 3px;
		display: none;
		gap: 2px;
	}

	.lq-chat-item:hover .lq-chat-row-actions {
		display: flex;
	}

	.lq-chat-row-action {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 20px;
		height: 20px;
		padding: 0;
		border: 0;
		border-radius: 3px;
		background: transparent;
		color: var(--lq-text-tertiary);
		cursor: pointer;
	}

	.lq-chat-row-action:hover {
		background: var(--lq-accent-soft);
		color: var(--lq-accent);
	}

	.lq-chat-row-input {
		display: block;
		width: calc(100% - 40px);
		margin: 3px 20px;
		padding: 3px 6px;
		font-size: 14px;
		border: 1px solid var(--lq-accent);
		border-radius: 2px;
		background: var(--lq-canvas);
		color: var(--lq-text);
	}
</style>
