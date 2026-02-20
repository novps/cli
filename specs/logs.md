Необходимо добавить новую команду, которая позволяет выводить логи ресурса. Должно быть два режима работы:

1. Вывод последних N записей (по умолчанию 100)
2. Режим --follow, который работает как tail -f

Новый эндпоинт: GET /public-api/resources/{resource_id}/logs                                                                                                                                                                           
   
  - Auth: PAT + APPS_READ permission                                                                                                                                                                                                                                                                                                                                                            
  - Query params:
    - start / end — время в наносекундах (обязательные)
    - limit — 1-5000, default 100
    - direction — backward / forward, default backward
    - search — substring фильтр (LogQL |=)
    - pod — фильтр по конкретному поду

Пример кода на typescript, который демонстрирует использование похожего метода:

```typescript jsx

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Badge,
  Box,
  Button,
  Divider,
  HStack,
  Icon, IconButton,
  Input,
  Select,
  Skeleton,
  Text,
  VStack,
} from "@chakra-ui/react";
import { GiShrug } from "react-icons/gi";
import useSWR from "swr";
import useSWRInfinite from "swr/infinite";
import Convert from "ansi-to-html";

import { ProjectAppResourceDetails } from "@/types";
import { formatDateTimeShortSeconds } from "@/utils";
import { getLogs } from "@/actions/resources/getLogs";
import {IoPause, IoPlay, IoRefresh} from "react-icons/io5";

const convert = new Convert({ escapeXML: true });

type Props = { resource: ProjectAppResourceDetails };

type LokiStreamResult = {
  stream: Record<string, string>;
  values: [string, string][]; // [tsNs, line]
};

type GetLogsResponse = {
  result: LokiStreamResult[];
  stats?: any;
};

type RangePreset =
  | "1m"
  | "5m"
  | "15m"
  | "30m"
  | "1h"
  | "4h"
  | "6h"
  | "8h"
  | "12h"
  | "1d"
  | "2d"
  | "7d"
  | "custom";

const PRESETS: { value: RangePreset; label: string; ms: number | null }[] = [
  { value: "1m", label: "Last 1 minute", ms: 1 * 60 * 1000 },
  { value: "5m", label: "Last 5 minutes", ms: 5 * 60 * 1000 },
  { value: "15m", label: "Last 15 minutes", ms: 15 * 60 * 1000 },
  { value: "30m", label: "Last 30 minutes", ms: 30 * 60 * 1000 },
  { value: "1h", label: "Last 1 hour", ms: 60 * 60 * 1000 },
  { value: "4h", label: "Last 4 hours", ms: 4 * 60 * 60 * 1000 },
  { value: "6h", label: "Last 6 hours", ms: 6 * 60 * 60 * 1000 },
  { value: "8h", label: "Last 8 hours", ms: 8 * 60 * 60 * 1000 },
  { value: "12h", label: "Last 12 hours", ms: 12 * 60 * 60 * 1000 },
  { value: "1d", label: "Last 1 day", ms: 24 * 60 * 60 * 1000 },
  { value: "2d", label: "Last 2 days", ms: 2 * 24 * 60 * 60 * 1000 },
  { value: "7d", label: "Last 7 days", ms: 7 * 24 * 60 * 60 * 1000 },
  { value: "custom", label: "Custom (last 7 days)", ms: null },
];

const PAGE_SIZE = 200;
const HEAD_SIZE = 200;
const TAIL_REFRESH_MS = 3000;

function dateTimeLocalValue(d: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

function clampToLast7Days(date: Date) {
  const max = new Date();
  const min = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
  if (date.getTime() > max.getTime()) return max;
  if (date.getTime() < min.getTime()) return min;
  return date;
}

function flattenResult(res?: LokiStreamResult[]) {
  return (res ?? []).flatMap((r) => r.values).filter(Boolean) as [string, string][];
}

function sortDescByTs(arr: [string, string][]) {
  arr.sort((a, b) => (BigInt(b[0]) > BigInt(a[0]) ? 1 : -1));
  return arr;
}

function uniqueByTsAndLine(arr: [string, string][]) {
  const seen = new Set<string>();
  const out: [string, string][] = [];
  for (const [ts, line] of arr) {
    const k = `${ts}::${line}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push([ts, line]);
  }
  return out;
}

function getPresetMs(preset: RangePreset) {
  return PRESETS.find((p) => p.value === preset)?.ms ?? 15 * 60 * 1000;
}

export function LogsExplorer({ resource }: Props) {
  const [preset, setPreset] = useState<RangePreset>("15m");

  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const defaultEnd = useMemo(() => new Date(), []);
  const defaultStart = useMemo(() => new Date(Date.now() - 15 * 60 * 1000), []);
  const [customStart, setCustomStart] = useState(dateTimeLocalValue(defaultStart));
  const [customEnd, setCustomEnd] = useState(dateTimeLocalValue(defaultEnd));

  const [isPaused, setIsPaused] = useState(false);
  const [newCount, setNewCount] = useState(0);

  // This is "next start" for tail polling (forward).
  const [headFromNs, setHeadFromNs] = useState<string | null>(null);

  const scrollBoxRef = useRef<HTMLDivElement | null>(null);
  const [isAtTop, setIsAtTop] = useState(true);

  // debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Fixed (user-selected) range for HISTORY paging.
  // For presets это "снимок" на момент выбора — tail будет приносить новые строки поверх.
  const historyRange = useMemo(() => {
    const endMs = Date.now();

    if (preset !== "custom") {
      const ms = getPresetMs(preset);
      const startMs = endMs - ms;
      return {
        startNs: String(startMs * 1_000_000),
        endNs: String(endMs * 1_000_000),
      };
    }

    const start = clampToLast7Days(new Date(customStart));
    const end = clampToLast7Days(new Date(customEnd));
    const startMs = Math.min(start.getTime(), end.getTime());
    const endMsFixed = Math.max(start.getTime(), end.getTime());

    return {
      startNs: String(startMs * 1_000_000),
      endNs: String(endMsFixed * 1_000_000),
    };
  }, [preset, customStart, customEnd]);

  // reset tail when filters change (NO scrolling!)
  useEffect(() => {
    setHeadFromNs(null);
    setNewCount(0);
    setIsPaused(false);
  }, [resource.internal_domain, historyRange.startNs, historyRange.endNs, debouncedSearch]);

  // -------- HISTORY (older) --------
  const getHistoryKey = (pageIndex: number, previousPageData: GetLogsResponse | null) => {
    const base = {
      key: "getLogs-history",
      resourceInternalDomain: resource.internal_domain,
      startNs: historyRange.startNs,
      endNs: historyRange.endNs,
      limit: PAGE_SIZE,
      direction: "backward" as const,
      search: debouncedSearch,
    };

    if (pageIndex === 0) return base;

    const prevFlat = flattenResult(previousPageData?.result);
    if (!prevFlat.length) return null;

    let oldest = prevFlat[0][0];
    for (const [ts] of prevFlat) {
      if (BigInt(ts) < BigInt(oldest)) oldest = ts;
    }

    return { ...base, endNs: (BigInt(oldest) - BigInt(1)).toString() };
  };

  const historyFetcher = async (k: any) => {
    const { key: _k, ...args } = k;
    return (await getLogs(args)) as GetLogsResponse;
  };

  const {
    data: historyPages,
    error: historyError,
    isValidating: isValidatingHistory,
    size,
    setSize,
    mutate: mutateHistory,
  } = useSWRInfinite(getHistoryKey, historyFetcher, { revalidateOnFocus: false });

  const historyLastPage =
    (historyPages ?? [])[Math.max(0, (historyPages?.length ?? 1) - 1)];
  const hasMoreOlder = flattenResult(historyLastPage?.result).length > 0;

  // -------- TAIL (newest only) --------
  // key существует только если:
  // - not paused
  // - scrolled to very top
  const headKey = useMemo(() => {
    if (isPaused) return null;
    if (!isAtTop) return null;

    return {
      key: "getLogs-head",
      resourceInternalDomain: resource.internal_domain,
      // we pass enough info for fetcher to compute "now" range:
      preset,
      presetMs: preset === "custom" ? null : getPresetMs(preset),
      customStart,
      customEnd,
      headFromNs,
      search: debouncedSearch,
      limit: HEAD_SIZE,
    };
  }, [
    isPaused,
    isAtTop,
    resource.internal_domain,
    preset,
    customStart,
    customEnd,
    headFromNs,
    debouncedSearch,
  ]);

  const headFetcher = async (k: any) => {
    const { key: _k, preset, presetMs, customStart, customEnd, headFromNs, search, limit, resourceInternalDomain } = k;

    // compute rolling "now" end for tail
    const endMs = Date.now();
    const endNs = String(endMs * 1_000_000);

    let startNs: string;

    if (preset !== "custom") {
      const ms = presetMs ?? 15 * 60 * 1000;
      const startMs = endMs - ms;
      startNs = String(startMs * 1_000_000);
    } else {
      const start = clampToLast7Days(new Date(customStart));
      const end = clampToLast7Days(new Date(customEnd));
      const startMs = Math.min(start.getTime(), end.getTime());
      startNs = String(startMs * 1_000_000);
      // NOTE: custom end is fixed; but tail uses "now" end — иначе не будет новых строк.
      // Если хочешь строго в рамках customEnd — скажи, поменяю.
    }

    // if we already have a cursor, fetch only newer
    if (headFromNs && BigInt(headFromNs) > BigInt(startNs)) {
      startNs = headFromNs;
    }

    return (await getLogs({
      resourceInternalDomain,
      startNs,
      endNs,
      limit,
      direction: "forward",
      search,
    })) as GetLogsResponse;
  };

  const {
    data: headPage,
    error: headError,
    isValidating: isValidatingHead,
    mutate: mutateHead,
  } = useSWR(headKey, headFetcher, {
    refreshInterval: headKey ? TAIL_REFRESH_MS : 0,
    revalidateOnFocus: false,
    keepPreviousData: true,
  });

  const [tailEntries, setTailEntries] = useState<[string, string][]>([]);

  // Merge new head logs into tailEntries, advance cursor
  useEffect(() => {
    const incoming = sortDescByTs(flattenResult(headPage?.result).slice());
    if (!incoming.length) return;

    let maxTs = incoming[0][0];
    for (const [ts] of incoming) {
      if (BigInt(ts) > BigInt(maxTs)) maxTs = ts;
    }

    setHeadFromNs((prev) => {
      const next = (BigInt(maxTs) + BigInt(1)).toString();
      if (prev && BigInt(next) <= BigInt(prev)) return prev;
      return next;
    });

    setTailEntries((prev) => uniqueByTsAndLine(sortDescByTs([...incoming, ...prev])));

    // Если внезапно прилетело не в top-режиме (например вручную Refresh),
    // покажем бейдж новых строк.
    if (!isAtTop) setNewCount((c) => c + incoming.length);
  }, [headPage, isAtTop]);

  // Reset tailEntries on filter changes
  useEffect(() => {
    setTailEntries([]);
  }, [resource.internal_domain, historyRange.startNs, historyRange.endNs, debouncedSearch]);

  const allEntries = useMemo(() => {
    const historyEntries = sortDescByTs(
      flattenResult(historyPages?.flatMap((p) => p.result)).slice()
    );
    return uniqueByTsAndLine(sortDescByTs([...tailEntries, ...historyEntries]));
  }, [historyPages, tailEntries]);

  const isInitialLoading = !historyPages && !historyError;
  const showEmpty = !isInitialLoading && allEntries.length === 0;

  const handleScroll = () => {
    const el = scrollBoxRef.current;
    if (!el) return;

    const atTop = el.scrollTop <= 4;
    setIsAtTop(atTop);

    if (atTop) setNewCount(0);
  };

  // Set initial isAtTop once container exists
  useEffect(() => {
    const el = scrollBoxRef.current;
    if (!el) return;
    // next frame to ensure layout applied
    requestAnimationFrame(() => {
      setIsAtTop(el.scrollTop <= 4);
    });
  }, [showEmpty]);

  return (
    <>
      <Box w="100%" mb={3}>
        <VStack align="stretch" spacing={3}>
          <HStack spacing={3} align="center">
            <Input
              placeholder="Search in logs…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />

            <Select
              value={preset}
              onChange={(e) => setPreset(e.target.value as RangePreset)}
              w="260px"
            >
              {PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </Select>

            <IconButton
              aria-label="Refresh"
              icon={<IoRefresh/>}
              onClick={() => {
                mutateHead();
                mutateHistory();
              }}
              isLoading={isValidatingHead || isValidatingHistory}
              variant="outline"
            />

            <IconButton
              aria-label={isPaused ? "Resume" : "Pause"}
              icon={isPaused ? <IoPlay/> : <IoPause/>}
              onClick={() => setIsPaused((p) => !p)}
              variant={isPaused ? "solid" : "outline"}
            />
            <Box flex="1" />
          </HStack>

          {preset === "custom" && (
            <HStack spacing={3}>
              <Box>
                <Text fontSize="xs" color="fg.muted" mb={1}>
                  Start (last 7 days)
                </Text>
                <Input
                  type="datetime-local"
                  value={customStart}
                  onChange={(e) => setCustomStart(e.target.value)}
                />
              </Box>

              <Box>
                <Text fontSize="xs" color="fg.muted" mb={1}>
                  End (last 7 days)
                </Text>
                <Input
                  type="datetime-local"
                  value={customEnd}
                  onChange={(e) => setCustomEnd(e.target.value)}
                />
              </Box>

              <Box flex="1" />
            </HStack>
          )}
        </VStack>
      </Box>

      {isInitialLoading && <Skeleton w="100%" h="500px" />}

      {showEmpty && (
        <Box w="100%" p={5}>
          <VStack w="100%" mt={5}>
            <Icon as={GiShrug} color="fg.muted" boxSize={20} />
            <Text>No logs match your search. Please try a different time range.</Text>
          </VStack>
        </Box>
      )}

      {!showEmpty && allEntries.length > 0 && (
        <Box
          w="100%"
          bg="gray.900"
          color="white"
          fontFamily="monospace"
          borderRadius="md"
          height="800px"
          overflow="hidden"
        >
          <Box
            ref={scrollBoxRef}
            onScroll={handleScroll}
            height="800px"
            overflowY="auto"
          >
            <Box
              position="sticky"
              top="0"
              bg="rgba(0,0,0,0.35)"
              backdropFilter="blur(6px)"
              p={2}
              zIndex={1}
            >
              <HStack justify="space-between">
                <Button
                  size="sm"
                  onClick={() => setSize((s) => s + 1)}
                  isLoading={isValidatingHistory}
                  isDisabled={!hasMoreOlder}
                  variant="outline"
                >
                  {hasMoreOlder ? "Load older" : "No more older"}
                </Button>

                <Text fontSize="xs" color="gray.400">
                  {isPaused
                    ? "Paused"
                    : isAtTop
                      ? "Tailing…"
                      : "Tailing off (scroll to top)"}
                </Text>
              </HStack>
            </Box>

            <VStack w="100%" spacing={0} align="stretch">
              {allEntries.map(([tsNs, line]) => (
                <HStack
                  key={`log-${tsNs}-${line.slice(0, 32)}`}
                  w="100%"
                  _hover={{ cursor: "pointer", bg: "gray.700" }}
                  px={3}
                  py={2}
                  align="start"
                  spacing={3}
                >
                  <Text fontSize="xs" color="gray.500" whiteSpace="nowrap">
                    {formatDateTimeShortSeconds(
                      new Date(Number(tsNs) / 1e6).toISOString()
                    )}
                  </Text>

                  <Text
                    fontSize="xs"
                    whiteSpace="pre-wrap"
                    wordBreak="break-word"
                    dangerouslySetInnerHTML={{ __html: convert.toHtml(line) }}
                  />
                </HStack>
              ))}
            </VStack>
          </Box>
        </Box>
      )}
    </>
  );
}
```
