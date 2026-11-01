"use client";

import { useTranslations } from "next-intl";
import { useRef, useState, type FormEvent } from "react";

import { useErrorMessage } from "@/components/common/error-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { Conversation } from "@/lib/api/types";
import { useDeleteConversation, useRenameConversation } from "@/lib/queries";

const MAX_TITLE = 200;

/**
 * Close handler that sends focus back to the row's menu button. The menu has closed by the time
 * the dialog opens, so Radix has nothing to return to, and the panel exists twice (side and sheet).
 */
function useFocusRowActions(conversation: Conversation | null) {
  const last = useRef<string | null>(null);
  return {
    onOpenAutoFocus: () => {
      last.current = conversation?.id ?? null;
    },
    onCloseAutoFocus: (event: Event) => {
      const buttons = document.querySelectorAll<HTMLElement>(`[data-conversation-actions="${last.current}"]`);
      const shown = Array.from(buttons).find((button) => button.offsetParent !== null);
      if (!shown) return;
      event.preventDefault();
      shown.focus();
    },
  };
}

function RenameForm({
  conversation,
  datasetId,
  onClose,
}: {
  conversation: Conversation;
  datasetId: string;
  onClose: () => void;
}) {
  const t = useTranslations("chat");
  const message = useErrorMessage();
  const rename = useRenameConversation(datasetId);
  const [title, setTitle] = useState(conversation.title);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    rename.mutate({ id: conversation.id, title: title.trim() }, { onSuccess: onClose });
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="conversation-title">{t("renameLabel")}</Label>
        <Input
          id="conversation-title"
          dir="auto"
          value={title}
          maxLength={MAX_TITLE}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      {rename.error ? (
        <p role="alert" className="text-sm text-bad">
          {message(rename.error)}
        </p>
      ) : null}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          {t("renameCancel")}
        </Button>
        <Button type="submit" disabled={!title.trim() || rename.isPending}>
          {t("renameSave")}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function RenameDialog({
  conversation,
  datasetId,
  onClose,
}: {
  conversation: Conversation | null;
  datasetId: string;
  onClose: () => void;
}) {
  const t = useTranslations("chat");
  const focusRow = useFocusRowActions(conversation);
  return (
    <Dialog open={Boolean(conversation)} onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent aria-describedby={undefined} {...focusRow}>
        <DialogHeader>
          <DialogTitle>{t("renameTitle")}</DialogTitle>
        </DialogHeader>
        {conversation ? (
          <RenameForm key={conversation.id} conversation={conversation} datasetId={datasetId} onClose={onClose} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export function DeleteDialog({
  conversation,
  datasetId,
  onClose,
  onDeleted,
}: {
  conversation: Conversation | null;
  datasetId: string;
  onClose: () => void;
  onDeleted: (id: string) => void;
}) {
  const t = useTranslations("chat");
  const message = useErrorMessage();
  const remove = useDeleteConversation(datasetId);
  const focusRow = useFocusRowActions(conversation);
  return (
    <Dialog open={Boolean(conversation)} onOpenChange={(open) => (open ? null : onClose())}>
      <DialogContent {...focusRow}>
        <DialogHeader>
          <DialogTitle>{t("deleteTitle")}</DialogTitle>
          <DialogDescription>{t("deleteBody", { title: conversation?.title ?? "" })}</DialogDescription>
        </DialogHeader>
        {remove.error ? (
          <p role="alert" className="text-sm text-bad">
            {message(remove.error)}
          </p>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            {t("deleteCancel")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => {
              if (!conversation) return;
              remove.mutate(conversation.id, {
                onSuccess: () => {
                  onDeleted(conversation.id);
                  onClose();
                },
              });
            }}
          >
            {t("deleteConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
