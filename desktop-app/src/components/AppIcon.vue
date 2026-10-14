<script setup>
defineProps({ name: { type: String, required: true }, size: { type: [Number, String], default: 18 } });
const paths = {
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  layers: 'm12 3 9 5-9 5-9-5 9-5zM3 12l9 5 9-5M3 16l9 5 9-5',
  search: 'm21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  bookmark: 'M6 4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v17l-6-4-6 4V4z',
  star: 'm12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3z',
  edit: 'm15 5 4 4M4 20l4-1 12-12a2.8 2.8 0 0 0-4-4L4 15l-1 6 5-2',
  folder: 'M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11H3V7z',
  external: 'M14 3h7v7M21 3 10 14M10 3H4a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-6',
  refresh: 'M20 7v5h-5M4 17v-5h5M5.5 7a7 7 0 0 1 11.6-2L20 8M4 16l2.9 3A7 7 0 0 0 18.5 17',
  sliders: 'M4 5h7m4 0h5M4 12h2m4 0h10M4 19h10m4 0h2M11 2v6M6 9v6M14 16v6',
  check: 'm5 12 4 4L19 6',
  select: 'M4 4h16v16H4zM8 12l3 3 5-6',
  panel: 'M3 4h18v16H3zM15 4v16',
  plus: 'M12 5v14M5 12h14',
  close: 'm6 6 12 12M18 6 6 18',
  chevron: 'm9 5 7 7-7 7',
  download: 'M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4',
  play: 'm8 5 11 7-11 7V5z',
  pause: 'M8 5v14M16 5v14',
  skip: 'm5 5 10 7-10 7V5zM19 5v14',
  stop: 'M6 6h12v12H6z',
  trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
  globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18',
  queue: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
};
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="app-icon">
    <path :d="paths[name] || paths.grid" />
  </svg>
</template>

<style scoped>
.app-icon { display: inline-block; flex: 0 0 auto; vertical-align: middle; }
</style>
