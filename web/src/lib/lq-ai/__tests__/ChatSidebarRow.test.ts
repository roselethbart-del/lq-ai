/**
 * Unit tests for ChatSidebarRow helpers (chat rename/delete in the
 * Project-grouped chat sidebar).
 *
 * Convention note: this codebase does not install @testing-library/svelte
 * (see AttachKBModal.test.ts / AttachedSkillPill.test.ts). The .svelte file
 * exports its pure logic from <script context="module">; we exercise those
 * helpers here.
 *
 * Coverage:
 *   - deleteConfirmMessage(chat)              → confirm() prompt copy
 *   - resolveRenameCommit(draft, currentTitle) → commit/no-commit decision
 *   - CHAT_TITLE_MAX_LENGTH                    → matches the backend cap
 */
import { describe, expect, it } from 'vitest';
import {
	CHAT_TITLE_MAX_LENGTH,
	deleteConfirmMessage,
	resolveRenameCommit
} from '../components/ChatSidebarRow.svelte';
import type { Chat } from '../types';

function makeChat(overrides: Partial<Chat> = {}): Chat {
	return {
		id: 'c1',
		title: 'NDA review for Acme deal',
		owner_id: 'u1',
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		...overrides
	};
}

describe('deleteConfirmMessage', () => {
	it('includes the chat title in the confirmation prompt', () => {
		expect(deleteConfirmMessage(makeChat({ title: 'HKA Service Agreement' }))).toBe(
			'Delete "HKA Service Agreement"? This cannot be undone.'
		);
	});

	it('falls back to "Untitled chat" when the title is empty', () => {
		expect(deleteConfirmMessage(makeChat({ title: '' }))).toBe(
			'Delete "Untitled chat"? This cannot be undone.'
		);
	});
});

describe('resolveRenameCommit', () => {
	it('returns the trimmed title when it differs from the current title', () => {
		expect(resolveRenameCommit('  Renamed chat  ', 'Original')).toBe('Renamed chat');
	});

	it('returns null when the draft is empty or whitespace-only', () => {
		expect(resolveRenameCommit('', 'Original')).toBeNull();
		expect(resolveRenameCommit('   ', 'Original')).toBeNull();
	});

	it('returns null when the trimmed draft equals the current title (no-op)', () => {
		expect(resolveRenameCommit('Original', 'Original')).toBeNull();
		expect(resolveRenameCommit('  Original  ', 'Original')).toBeNull();
	});
});

describe('CHAT_TITLE_MAX_LENGTH', () => {
	it('matches the backend TITLE_MAX_LEN (api/app/schemas/chats.py)', () => {
		// Regression guard: this bounds the rename <input>'s maxlength so a
		// title over the backend's cap fails client-side instead of round-
		// tripping to a silent 422 (the row would otherwise just revert with
		// no feedback — see ChatPanel.svelte's renameChat error handling).
		expect(CHAT_TITLE_MAX_LENGTH).toBe(200);
	});
});
