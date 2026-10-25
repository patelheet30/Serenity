<script lang="ts" generics="T extends string">
	import Icon from './Icon.svelte';

	type Option = { value: T; label: string; hint?: string };
	// A searchable picker (channels, roles, members). onsearch lets the parent fetch options.
	let {
		value = $bindable(),
		options,
		label,
		placeholder = 'Pick…',
		onsearch
	}: {
		value: T | null;
		options: Option[];
		label: string;
		placeholder?: string;
		onsearch?: (q: string) => void;
	} = $props();

	let open = $state(false);
	let q = $state('');
	let sel = $state(0);
	let root: HTMLElement | undefined = $state();
	const uid = $props.id();

	const shown = $derived(
		onsearch ? options : options.filter((o) => o.label.toLowerCase().includes(q.toLowerCase()))
	);
	const current = $derived(options.find((o) => o.value === value));

	function pick(o: Option) {
		value = o.value;
		open = false;
	}

	function onkeydown(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') sel = (sel + 1) % Math.max(1, shown.length);
		else if (e.key === 'ArrowUp') sel = (sel - 1 + shown.length) % Math.max(1, shown.length);
		else if (e.key === 'Enter' && shown[sel]) pick(shown[sel]);
		else if (e.key === 'Escape') open = false;
		else return;
		e.preventDefault();
		e.stopPropagation();
	}
</script>

<svelte:window onclick={(e) => open && !root?.contains(e.target as Node) && (open = false)} />

<div class="sel" bind:this={root}>
	<button class="btn" aria-haspopup="listbox" aria-expanded={open} aria-label="{label}: {current?.label ?? 'none'}" onclick={() => ((open = !open), (q = ''), (sel = 0))}>
		<span class:dim={!current}>{current?.label ?? placeholder}</span><Icon name="chev" />
	</button>
	{#if open}
		<div class="popover">
			<div class="search">
				<Icon name="search" />
				<!-- svelte-ignore a11y_autofocus -->
				<input
					class="inp"
					type="text"
					role="combobox"
					aria-label="Search {label}"
					aria-controls="{uid}-list"
					aria-expanded="true"
					aria-activedescendant={shown[sel] ? `${uid}-${sel}` : undefined}
					autocomplete="off"
					autofocus
					bind:value={q}
					oninput={() => ((sel = 0), onsearch?.(q))}
					{onkeydown}
				/>
			</div>
			<ul role="listbox" id="{uid}-list" aria-label={label}>
				{#each shown as o, i (o.value)}
					<li role="presentation">
						<button role="option" id="{uid}-{i}" aria-selected={i === sel} tabindex="-1" onclick={() => pick(o)}>
							{o.label}{#if o.hint}<span class="dim" style="margin-left:auto">{o.hint}</span>{/if}
						</button>
					</li>
				{:else}
					<li class="empty" style="padding:12px">No matches</li>
				{/each}
			</ul>
		</div>
	{/if}
</div>

<style>
	.sel {
		position: relative;
		display: inline-block;
	}
	.sel .popover {
		left: 0;
		right: auto;
		top: 38px;
	}
	.sel li [aria-selected='true'] {
		background: var(--ink3);
	}
</style>
