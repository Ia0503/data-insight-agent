<script setup lang="ts">
import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  ref,
  useId,
  watch,
} from "vue";

const props = defineProps<{
  modelValue?: string;
  options: { value: string; label: string }[];
  controlLabel: string;
  disabled?: boolean;
  required?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const id = useId();
const root = ref<HTMLElement | null>(null);
const trigger = ref<HTMLButtonElement | null>(null);
const open = ref(false),
  active = ref(0),
  above = ref(false),
  invalid = ref(false);
const selected = computed(() =>
  props.options.findIndex((o) => o.value === (props.modelValue ?? "")),
);
const label = computed(() => props.options[selected.value]?.label ?? "请选择");
let prefix = "",
  prefixTimer: ReturnType<typeof setTimeout> | undefined;
function blocked() {
  return props.disabled || trigger.value?.matches(":disabled");
}
async function reveal() {
  if (blocked()) return;
  active.value = Math.max(0, selected.value);
  const rect = trigger.value?.getBoundingClientRect();
  above.value = !!rect && innerHeight - rect.bottom < 260 && rect.top > 260;
  open.value = true;
  await showActive();
}
async function showActive() {
  await nextTick();
  root.value
    ?.querySelector('[data-active="true"]')
    ?.scrollIntoView({ block: "nearest" });
}
function choose(index: number) {
  if (blocked() || !props.options[index]) return;
  emit("update:modelValue", props.options[index]!.value);
  invalid.value = false;
  open.value = false;
  trigger.value?.focus({ preventScroll: true });
}
function keydown(event: KeyboardEvent) {
  if (blocked()) return;
  if (event.key === "Escape" || event.key === "Tab") {
    open.value = false;
    return;
  }
  if (["Enter", " "].includes(event.key)) {
    event.preventDefault();
    if (open.value) choose(active.value);
    else void reveal();
    return;
  }
  if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    if (!open.value) {
      void reveal();
      return;
    }
    active.value =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? props.options.length - 1
          : Math.max(
              0,
              Math.min(
                props.options.length - 1,
                active.value + (event.key === "ArrowDown" ? 1 : -1),
              ),
            );
    void showActive();
  } else if (
    event.key.length === 1 &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.altKey
  ) {
    event.preventDefault();
    if (!open.value) void reveal();
    prefix += event.key.toLocaleLowerCase();
    clearTimeout(prefixTimer);
    prefixTimer = setTimeout(() => (prefix = ""), 700);
    const index = props.options.findIndex((o) =>
      o.label.toLocaleLowerCase().startsWith(prefix),
    );
    if (index >= 0) {
      active.value = index;
      void showActive();
    }
  }
}
function outside(event: PointerEvent) {
  if (!root.value?.contains(event.target as Node)) open.value = false;
}
function validate(event: Event) {
  invalid.value = true;
  // 表单会对每个必填项触发 invalid；只展开第一个，避免多个菜单同时抢焦点。
  const native = event.target as HTMLSelectElement;
  if (native.form?.querySelector("select:invalid") !== native) return;
  trigger.value?.focus();
  void reveal();
}
watch(
  () => [props.disabled, props.modelValue],
  () => {
    open.value = false;
    invalid.value = false;
  },
);
onMounted(() => document.addEventListener("pointerdown", outside));
onUnmounted(() => {
  document.removeEventListener("pointerdown", outside);
  clearTimeout(prefixTimer);
});
</script>

<template>
  <div
    ref="root"
    class="select-field"
    @focusout="
      (event) => {
        if (!root?.contains(event.relatedTarget as Node)) open = false;
      }
    "
  >
    <button
      ref="trigger"
      type="button"
      class="select-trigger"
      role="combobox"
      aria-haspopup="listbox"
      :aria-label="controlLabel"
      :aria-expanded="open"
      :aria-controls="`${id}-list`"
      :aria-activedescendant="open ? `${id}-${active}` : undefined"
      :aria-required="required || undefined"
      :aria-invalid="invalid || undefined"
      :aria-describedby="invalid ? `${id}-error` : undefined"
      :disabled="disabled"
      @click="open ? (open = false) : reveal()"
      @keydown="keydown"
    >
      <span>{{ label }}</span
      ><span class="select-chevron" aria-hidden="true">⌄</span>
    </button>
    <!-- 保留原生表单约束；无效时将焦点交给可见控件，避免隐藏字段无法定位。 -->
    <select
      class="select-native"
      tabindex="-1"
      aria-hidden="true"
      :value="modelValue ?? ''"
      :required="required"
      :disabled="disabled"
      @invalid.prevent="validate"
    >
      <option
        v-for="option in options"
        :key="option.value"
        :value="option.value"
      >
        {{ option.label }}
      </option>
    </select>
    <ul
      v-if="open"
      :id="`${id}-list`"
      role="listbox"
      :aria-label="controlLabel"
      class="select-options"
      :class="{ 'opens-above': above }"
    >
      <li
        v-for="(option, index) in options"
        :key="option.value"
        role="presentation"
      >
        <button
          :id="`${id}-${index}`"
          type="button"
          role="option"
          tabindex="-1"
          :aria-selected="option.value === (modelValue ?? '')"
          :data-active="index === active"
          @pointerdown.prevent
          @pointermove="active = index"
          @click="choose(index)"
        >
          <span>{{ option.label }}</span
          ><span v-if="option.value === (modelValue ?? '')" aria-hidden="true"
            >✓</span
          >
        </button>
      </li>
    </ul>
    <span v-if="invalid" :id="`${id}-error`" class="select-error"
      >请选择{{ controlLabel }}</span
    >
  </div>
</template>
