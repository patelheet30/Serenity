<script lang="ts">
	import { palette, search, type Item } from '../palette.svelte';
	import Icon from './Icon.svelte';

	let dialog: HTMLDialogElement | undefined = $state();
	let input: HTMLInputElement | undefined = $state();
	let sel = $state(0);
	let returnTo: HTMLElement | null = null;

	const items = $derived(palette.open ? search(palette.query) : []);

	$effect(() => {
		if (palette.open && !dialog?.open) {
			returnTo = document.activeElement as HTMLElement | null;
			sel = 0;
			dialog?.showModal();
			input?.focus();
		}
		if (!palette.open && dialog?.open) dialog.close();
	});

	function runItem(it: Item | undefined) {
		if (!it) return;
		if (it.fill && !it.run) {
			palette.query = it.fill;
			sel = 0;
			input?.focus();
			return;
		}
		returnTo = null; // the command decides where focus goes
		palette.open = false;
		it.run?.();
	}

	function onkeydown(e: KeyboardEvent) {
		const n = Math.max(1, items.length);
		if (e.key === 'ArrowDown') sel = (sel + 1) % n;
		else if (e.key === 'ArrowUp') sel = (sel - 1 + n) % n;
		else if (e.key === 'Enter') runItem(items[sel]);
		else if (e.key === 'Tab' && items[sel]) {
			palette.query = items[sel].fill ?? `${items[sel].label.toLowerCase()} `;
			sel = 0;
		} else if (e.key === '?' && !palette.query) {
			palette.open = false;
			palette.keys = true;
		} else return;
		e.preventDefault();
	}

	$effect(() => {
		document.getElementById(`po-${sel}`)?.scrollIntoView({ block: 'nearest' });
	});
</script>

<dialog
	class="pal"
	aria-label="Command palette"
	bind:this={dialog}
	onclose={() => {
		palette.open = false;
		returnTo?.focus();
	}}
	onclick={(e) => e.target === dialog && (palette.open = false)}
>
	<div class="pal-box">
		<div class="pal-in">
			<Icon name="search" />
			<input
				bind:this={input}
				bind:value={palette.query}
				oninput={() => (sel = 0)}
				{onkeydown}
				type="text"
				role="combobox"
				aria-label="Command"
				aria-expanded="true"
				aria-controls="palList"
				aria-autocomplete="list"
				aria-activedescendant={items.length ? `po-${sel}` : undefined}
				autocomplete="off"
				spellcheck="false"
				placeholder="Type a command or search…"
			/>
			<kbd>esc</kbd>
		</div>
		<ul id="palList" class="pal-list" role="listbox" aria-label="Results">
			{#each items as it, i (i)}
				{#if i === 0 || items[i - 1].group !== it.group}
					<li class="g" role="presentation">{it.group}</li>
				{/if}
				<!-- svelte-ignore a11y_click_events_have_key_events (the combobox input handles keys) -->
				<li
					role="option"
					id="po-{i}"
					aria-selected={i === sel}
					class:run={it.parsed}
					onclick={() => runItem(it)}
					onmousemove={() => (sel = i)}
				>
					<Icon name={it.icon} /><span class="lb">{it.label}</span>
					{#if it.hint}<span class="hn">{it.hint}</span>{/if}
				</li>
			{:else}
				<li class="g" role="presentation" style="padding:18px 10px">No matches.</li>
			{/each}
		</ul>
		<div class="pal-foot">
			<span><kbd>↑</kbd> <kbd>↓</kbd> move</span><span><kbd>↵</kbd> run</span><span
				><kbd>tab</kbd> complete</span
			><span><kbd>?</kbd> all shortcuts</span>
		</div>
	</div>
</dialog>
