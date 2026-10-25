<script lang="ts" generics="T">
	import type { Snippet } from 'svelte';

	type Column = { key: string; label: string; align?: 'right' };
	// Sticky header; j/k or ↑/↓ move between rows, Enter opens one. `cell` renders custom cells.
	let {
		rows,
		columns,
		key,
		label,
		cell,
		onopen,
		selected = null,
		maxHeight = '480px'
	}: {
		rows: T[];
		columns: Column[];
		key: (row: T) => string;
		label: string;
		cell?: Snippet<[T, Column]>;
		onopen?: (row: T) => void;
		selected?: string | null;
		maxHeight?: string;
	} = $props();

	let active = $state(0);

	function onkeydown(e: KeyboardEvent) {
		const tr = e.currentTarget as HTMLElement;
		const rowsEls = [...tr.parentElement!.querySelectorAll<HTMLElement>(':scope > tr')];
		const i = rowsEls.indexOf(tr);
		if ((e.target as HTMLElement).tagName === 'INPUT') return;
		let next = i;
		if (e.key === 'j' || e.key === 'ArrowDown') next = Math.min(rowsEls.length - 1, i + 1);
		else if (e.key === 'k' || e.key === 'ArrowUp') next = Math.max(0, i - 1);
		else if (e.key === 'Enter' && e.target === rowsEls[i]) return onopen?.(rows[i]);
		else return;
		e.preventDefault();
		active = next;
		rowsEls[next].focus();
	}
</script>

<div class="scroll" style:max-height={maxHeight}>
	<table class="tbl" aria-label={label}>
		<thead>
			<tr>
				{#each columns as c (c.key)}<th scope="col" class:r={c.align === 'right'}>{c.label}</th>{/each}
			</tr>
		</thead>
		<tbody>
			{#each rows as row, i (key(row))}
				<tr
					tabindex={i === active ? 0 : -1}
					{onkeydown}
					class:sel={selected === key(row)}
					onclick={(e) => {
						active = i;
						if (!(e.target as HTMLElement).closest('button,input,a')) onopen?.(row);
					}}
				>
					{#each columns as c (c.key)}
						<td class:r={c.align === 'right'}>
							{#if cell}{@render cell(row, c)}{:else}{String((row as Record<string, unknown>)[c.key] ?? '')}{/if}
						</td>
					{/each}
				</tr>
			{/each}
		</tbody>
	</table>
</div>
