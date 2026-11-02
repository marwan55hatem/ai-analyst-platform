"use client";

import { useQueryClient } from "@tanstack/react-query";
import { BarChart3, PanelLeft } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState } from "@/components/common/empty-state";
import { ErrorState, useErrorMessage } from "@/components/common/error-state";
import { PageHeader } from "@/components/common/page-header";
import { DatasetPicker } from "@/components/insights/analysis-gate";
import { InsightDrawer } from "@/components/insights/insight-drawer";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useActiveDataset, type ActiveDataset } from "@/lib/active-dataset";
import type { Conversation, Dataset } from "@/lib/api/types";
import type { ChatTurn } from "@/lib/chat/events";
import { insightContext, removePrefill } from "@/lib/chat/insight-context";
import { selectStarters } from "@/lib/chat/starters";
import { showTurns, turnsFromMessages } from "@/lib/chat/turns";
import { useChatStream } from "@/lib/chat/use-chat-stream";
import {
  fetchMessages,
  keys,
  useCapabilities,
  useConversations,
  useCreateConversation,
  useInsight,
  useMessages,
} from "@/lib/queries";
import { useUrlUpdate } from "@/lib/url-state";

import { Announcer } from "./announcer";
import { Composer } from "./composer";
import { DeleteDialog, RenameDialog } from "./conversation-dialogs";
import { ConversationList } from "./conversation-list";
import { InsightChip } from "./insight-chip";
import { MessageList } from "./message-list";
import { StarterQuestions } from "./starter-questions";

/** One dataset's chat. It remounts when the dataset changes, which also ends any open stream. */
function ChatWorkspace({ active, dataset, version }: { active: ActiveDataset; dataset: Dataset; version: number }) {
  const t = useTranslations("chat");
  const errorMessage = useErrorMessage();
  const params = useSearchParams();
  const update = useUrlUpdate();
  const client = useQueryClient();
  const conversationId = params.get("c");
  const insightId = params.get("insight");

  const conversations = useConversations(dataset.id);
  const messages = useMessages(conversationId);
  const capabilities = useCapabilities(dataset.id, version, true);
  const create = useCreateConversation(dataset.id);
  const found = useInsight(insightId);

  const [draft, setDraft] = useState("");
  const [kept, setKept] = useState<Record<number, ChatTurn>>({});
  const [position, setPosition] = useState(0);
  const [prefilledFor, setPrefilledFor] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [renaming, setRenaming] = useState<Conversation | null>(null);
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const [openInsight, setOpenInsight] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const composerArea = useRef<HTMLDivElement>(null);

  const onSettled = useCallback(
    (id: string) => {
      void client.invalidateQueries({ queryKey: keys.messages(id) });
      void client.invalidateQueries({ queryKey: keys.conversations(dataset.id) });
      void client.invalidateQueries({ queryKey: keys.usage });
    },
    [client, dataset.id],
  );
  const fetchStored = useCallback(
    (id: string) =>
      client
        .fetchQuery({ queryKey: keys.messages(id), queryFn: () => fetchMessages(id), staleTime: 0 })
        .then(turnsFromMessages),
    [client],
  );
  const stream = useChatStream({ onSettled, fetchStored });

  const stored = useMemo(() => turnsFromMessages(messages.data ?? []), [messages.data]);
  const turns = useMemo(() => showTurns(stored, kept, stream.turn, position), [stored, kept, stream.turn, position]);
  const streaming = stream.turn?.status === "streaming";

  const context = insightContext({
    id: insightId,
    insight: found.data,
    failed: Boolean(found.error),
    datasets: active.datasets,
    activeId: dataset.id,
  });
  const prefill = context.kind === "ready" ? t("insight.prefill", { title: context.title }) : "";
  if (prefill && prefilledFor !== insightId) {
    setPrefilledFor(insightId);
    setDraft(prefill);
  }
  const switchTo = context.kind === "switch" ? context.datasetId : null;
  useEffect(() => {
    if (switchTo) update({ dataset: switchTo, c: null });
  }, [switchTo, update]);

  /** Show another conversation, or none: the stream and this session's turns stay with the old one. */
  const open = (id: string | null) => {
    stream.clear();
    setKept({});
    setPosition(0);
    setPanelOpen(false);
    update({ c: id });
    if (id) void client.invalidateQueries({ queryKey: keys.messages(id) });
    composerArea.current?.querySelector("textarea")?.focus();
  };

  /** Keep the finished turn on screen until the stored messages have caught up with it. */
  const keepLiveTurn = () => {
    const { turn } = stream;
    if (turn) setKept((previous) => ({ ...previous, [position]: turn }));
  };

  const ask = async (question: string) => {
    let id = conversationId;
    setCreateError(null);
    if (!id) {
      try {
        id = (await create.mutateAsync()).id;
      } catch (error) {
        setCreateError(errorMessage(error));
        return;
      }
      update({ c: id });
    }
    setDraft("");
    if (insightId) update({ insight: null });
    keepLiveTurn();
    setPosition(turns.length);
    void stream.ask(id, question);
  };

  const retry = (turn: ChatTurn) => {
    if (!conversationId) return;
    const index = turns.indexOf(turn);
    setKept((previous) => Object.fromEntries(Object.entries(previous).filter(([key]) => Number(key) !== index)));
    setPosition(index);
    void stream.retry(conversationId, turn.question);
  };

  const list = (
    <ConversationList
      conversations={conversations.data ?? []}
      currentId={conversationId}
      loading={conversations.isPending}
      onOpen={open}
      onNew={() => open(null)}
      onRename={setRenaming}
      onDelete={setDeleting}
    />
  );
  const starters = capabilities.data ? selectStarters(capabilities.data) : [];
  const loadingMessages = Boolean(conversationId) && messages.isPending && !turns.length;

  return (
    <div className="flex h-[calc(100dvh-3.5rem-3rem)] min-h-[28rem] flex-col lg:h-[calc(100dvh-3.5rem-4rem)]" data-testid="chat">
      {/* Not PageHeader: on a phone the conversation needs the height, so this stays one row. */}
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2 pb-4">
        <div className="min-w-0 flex-1 basis-40">
          <h1 className="text-xl font-semibold sm:text-2xl">{t("title")}</h1>
          {active.datasets.length < 2 ? (
            <p className="truncate text-sm text-muted-foreground" title={dataset.name}>
              {t("askingAbout", { name: dataset.name })}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <Sheet open={panelOpen} onOpenChange={setPanelOpen}>
            <SheetTrigger asChild>
              <Button type="button" variant="outline" className="lg:hidden">
                <PanelLeft className="rtl:-scale-x-100" aria-hidden="true" />
                {t("openConversations")}
              </Button>
            </SheetTrigger>
            <SheetContent side="start" className="w-80 max-w-[85vw] p-4">
              <SheetTitle className="text-lg">{t("conversations")}</SheetTitle>
              {list}
            </SheetContent>
          </Sheet>
          <DatasetPicker active={active} />
        </div>
      </div>
      <div className="grid min-h-0 flex-1 gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <aside aria-label={t("conversations")} className="hidden min-h-0 lg:block">
          {list}
        </aside>
        <div className="flex min-h-0 min-w-0 flex-col gap-3">
          {messages.error ? (
            <ErrorState title={t("loadFailed")} error={messages.error} onRetry={() => void messages.refetch()} />
          ) : loadingMessages ? (
            <div className="flex-1 space-y-4" aria-busy="true">
              <Skeleton className="ms-auto h-10 w-1/2" />
              <Skeleton className="h-40 w-full" />
            </div>
          ) : (
            <MessageList
              turns={turns}
              onRetry={retry}
              onOpenInsight={setOpenInsight}
              empty={
                <div className="max-w-2xl space-y-6 py-4" data-testid="chat-empty">
                  <div className="space-y-1">
                    <h2 className="text-xl font-semibold">{t("empty.title")}</h2>
                    <p className="text-muted-foreground">{t("empty.body")}</p>
                  </div>
                  {starters.length ? <StarterQuestions keys={starters} onAsk={(q) => void ask(q)} /> : null}
                </div>
              }
            />
          )}
          <div ref={composerArea} className="space-y-2">
            <InsightChip
              context={context}
              onRemove={() => {
                update({ insight: null });
                setDraft(removePrefill(draft, prefill));
              }}
            />
            {createError ? (
              <p role="alert" className="text-sm text-bad">
                {createError}
              </p>
            ) : null}
            <Composer
              value={draft}
              onChange={setDraft}
              onSend={(question) => void ask(question)}
              onStop={stream.stop}
              streaming={streaming}
              waiting={loadingMessages}
            />
          </div>
        </div>
      </div>
      <Announcer turn={stream.turn} />
      <RenameDialog conversation={renaming} datasetId={dataset.id} onClose={() => setRenaming(null)} />
      <DeleteDialog
        conversation={deleting}
        datasetId={dataset.id}
        onClose={() => setDeleting(null)}
        onDeleted={(id) => {
          if (id === conversationId) open(null);
        }}
      />
      <InsightDrawer id={openInsight} onClose={() => setOpenInsight(null)} />
    </div>
  );
}

/** Ask the analyst: a conversation about one dataset, with the evidence behind each answer. */
export function ChatPage() {
  const t = useTranslations("chat");
  const ti = useTranslations("insights");
  const active = useActiveDataset();
  if (active.loading) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label={ti("loading")}>
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }
  if (active.error) return <ErrorState title={t("loadFailed")} error={active.error} onRetry={active.retry} />;
  if (!active.dataset) {
    return (
      <>
        <PageHeader title={t("title")} description={t("description")} />
        <EmptyState
          icon={<BarChart3 />}
          title={ti("noData.title")}
          body={ti("noData.body")}
          action={
            <Button asChild>
              <Link href="/datasets/new">{ti("noData.cta")}</Link>
            </Button>
          }
        />
      </>
    );
  }
  return <ChatWorkspace key={active.dataset.id} active={active} dataset={active.dataset} version={active.version} />;
}
