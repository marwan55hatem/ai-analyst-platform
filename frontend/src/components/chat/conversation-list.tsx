"use client";

import { MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import type { Conversation } from "@/lib/api/types";
import { useFormat } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The user's conversations about the active dataset, most recent first. */
export function ConversationList({
  conversations,
  currentId,
  loading,
  onOpen,
  onNew,
  onRename,
  onDelete,
}: {
  conversations: Conversation[];
  currentId: string | null;
  loading: boolean;
  onOpen: (id: string) => void;
  onNew: () => void;
  onRename: (conversation: Conversation) => void;
  onDelete: (conversation: Conversation) => void;
}) {
  const t = useTranslations("chat");
  const fmt = useFormat();
  return (
    <div className="flex h-full min-h-0 flex-col gap-3" data-testid="conversation-list">
      <Button type="button" variant="outline" className="w-full justify-start" onClick={onNew}>
        <Plus aria-hidden="true" />
        {t("newConversation")}
      </Button>
      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : conversations.length ? (
        <ul className="-mx-1 min-h-0 flex-1 space-y-0.5 overflow-y-auto px-1 py-0.5">
          {conversations.map((conversation) => {
            const current = conversation.id === currentId;
            return (
              <li
                key={conversation.id}
                className={cn(
                  "group flex items-center gap-1 rounded-md hover:bg-accent",
                  current && "bg-card shadow-[inset_3px_0_0_var(--primary)] rtl:shadow-[inset_-3px_0_0_var(--primary)]",
                )}
              >
                <button
                  type="button"
                  aria-current={current ? "true" : undefined}
                  onClick={() => onOpen(conversation.id)}
                  className="min-w-0 flex-1 rounded-md px-3 py-2 text-start outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="block truncate text-sm font-medium" dir="auto">
                    {conversation.title}
                  </span>
                  <span className="block text-xs text-muted-foreground" data-testid="conversation-time">
                    {fmt.ago(conversation.updated_at)}
                  </span>
                </button>
                <DropdownMenu modal={false}>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={t("actions", { title: conversation.title })}
                      data-conversation-actions={conversation.id}
                    >
                      <MoreHorizontal aria-hidden="true" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onSelect={() => onRename(conversation)}>
                      <Pencil aria-hidden="true" />
                      {t("rename")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => onDelete(conversation)}>
                      <Trash2 aria-hidden="true" />
                      {t("delete")}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="px-1 text-sm text-muted-foreground">{t("noConversations")}</p>
      )}
    </div>
  );
}
