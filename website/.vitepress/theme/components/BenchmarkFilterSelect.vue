<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";

interface BenchmarkFilterOption {
  value: string;
  label: string;
}

const props = defineProps<{
  modelValue: string;
  label: string;
  options: BenchmarkFilterOption[];
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string];
}>();

const root = ref<HTMLElement | null>(null);
const trigger = ref<HTMLButtonElement | null>(null);
const isOpen = ref(false);
const activeIndex = ref(0);

const selectedIndex = computed(() => {
  const index = props.options.findIndex((option) => option.value === props.modelValue);
  return index >= 0 ? index : 0;
});
const selectedLabel = computed(
  () => props.options[selectedIndex.value]?.label ?? "请选择",
);

function openMenu(): void {
  activeIndex.value = selectedIndex.value;
  isOpen.value = true;
}

function toggleMenu(): void {
  if (isOpen.value) {
    isOpen.value = false;
    return;
  }
  openMenu();
}

function selectOption(option: BenchmarkFilterOption): void {
  emit("update:modelValue", option.value);
  isOpen.value = false;
  void nextTick(() => trigger.value?.focus());
}

function selectActiveOption(): void {
  const option = props.options[activeIndex.value];
  if (option) selectOption(option);
}

function moveActiveOption(offset: number): void {
  if (!props.options.length) return;
  activeIndex.value =
    (activeIndex.value + offset + props.options.length) % props.options.length;
  isOpen.value = true;
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    moveActiveOption(1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    moveActiveOption(-1);
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    if (isOpen.value) selectActiveOption();
    else openMenu();
  } else if (event.key === "Escape") {
    isOpen.value = false;
  }
}

function closeFromOutside(event: PointerEvent): void {
  if (root.value && !root.value.contains(event.target as Node)) {
    isOpen.value = false;
  }
}

onMounted(() => document.addEventListener("pointerdown", closeFromOutside));
onBeforeUnmount(() => document.removeEventListener("pointerdown", closeFromOutside));
</script>

<template>
  <div ref="root" class="benchmark-filter-select" :class="{ open: isOpen }">
    <span class="filter-label">{{ label }}</span>
    <button
      ref="trigger"
      type="button"
      class="filter-trigger"
      role="combobox"
      aria-haspopup="listbox"
      :aria-expanded="isOpen"
      @click="toggleMenu"
      @keydown="handleKeydown"
    >
      <span>{{ selectedLabel }}</span>
      <i class="filter-chevron" aria-hidden="true"></i>
    </button>

    <Transition name="filter-menu">
      <div v-if="isOpen" class="filter-menu" role="listbox" :aria-label="label">
        <button
          v-for="(option, index) in options"
          :key="option.value"
          type="button"
          class="filter-option"
          :class="{ active: index === activeIndex }"
          role="option"
          :aria-selected="option.value === modelValue"
          @mouseenter="activeIndex = index"
          @click="selectOption(option)"
        >
          <span>{{ option.label }}</span>
          <strong v-if="option.value === modelValue" aria-hidden="true">✓</strong>
        </button>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.benchmark-filter-select {
  position: relative;
  min-width: 0;
}

.filter-label {
  display: block;
  padding: 0 10px 5px;
  color: var(--benchmark-muted);
  font-size: 10px;
}

.filter-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  min-height: 42px;
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 0 13px;
  color: var(--benchmark-ink);
  font: inherit;
  font-size: 13px;
  text-align: left;
  background: var(--benchmark-paper);
  cursor: pointer;
}

.filter-trigger:focus-visible,
.open .filter-trigger {
  border-color: rgba(185, 28, 28, 0.42);
  outline: none;
  box-shadow: 0 0 0 3px rgba(185, 28, 28, 0.08);
}

.filter-chevron {
  width: 7px;
  height: 7px;
  border-right: 1.5px solid var(--benchmark-muted);
  border-bottom: 1.5px solid var(--benchmark-muted);
  transform: translateY(-2px) rotate(45deg);
  transition: transform 160ms ease;
}

.open .filter-chevron {
  transform: translateY(2px) rotate(225deg);
}

.filter-menu {
  position: absolute;
  z-index: 20;
  top: calc(100% + 7px);
  right: 0;
  left: 0;
  display: grid;
  max-height: 270px;
  overflow-y: auto;
  border: 1px solid var(--benchmark-border);
  border-radius: 8px;
  padding: 5px;
  background: var(--benchmark-paper);
  box-shadow: 0 18px 38px rgba(15, 23, 42, 0.14);
}

.filter-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 38px;
  border: 0;
  border-radius: 5px;
  padding: 0 10px;
  color: var(--benchmark-ink);
  font: inherit;
  font-size: 12px;
  text-align: left;
  background: transparent;
  cursor: pointer;
}

.filter-option:hover,
.filter-option.active {
  background: var(--benchmark-soft);
}

.filter-option strong {
  color: var(--benchmark-red);
}

.filter-menu-enter-active,
.filter-menu-leave-active {
  transition: opacity 120ms ease, transform 120ms ease;
}

.filter-menu-enter-from,
.filter-menu-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
