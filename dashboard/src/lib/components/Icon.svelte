<script lang="ts">
	import { ICONS, type IconName } from '../icons';

	let { name, style }: { name: IconName; style?: string } = $props();
</script>

<svg class="i" viewBox="0 0 16 16" aria-hidden="true" {style}><path d={ICONS[name]} /></svg>
