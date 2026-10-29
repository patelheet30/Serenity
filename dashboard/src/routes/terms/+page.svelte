<script lang="ts">
	import LegalPage from '#lib/components/LegalPage.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<LegalPage {...data} />
