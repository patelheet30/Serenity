<script lang="ts">
	import { hue, initials } from '../util';

	// kind="guild" gives the rounded-square server avatar; otherwise a round user avatar.
	let {
		name,
		src = null,
		kind = 'user',
		size = ''
	}: { name: string; src?: string | null; kind?: 'user' | 'guild'; size?: '' | 'xs' | 'lg' } =
		$props();

	const h = $derived(hue(name));
	const bg = $derived(
		kind === 'guild'
			? `linear-gradient(140deg,hsl(${h} 38% 70%),hsl(${(h + 50) % 360} 34% 52%))`
			: `linear-gradient(135deg,hsl(${h} 45% 72%),hsl(${(h + 40) % 360} 40% 58%))`
	);
	const cls = $derived(kind === 'guild' ? 'gav' : `av ${size}`);
</script>

{#if src}
	<img class={cls} {src} alt="" loading="lazy" style="object-fit:cover" />
{:else}
	<span class={cls} aria-hidden="true" style:background={bg}>{initials(name, kind === 'guild')}</span>
{/if}
