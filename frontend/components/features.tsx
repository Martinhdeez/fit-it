"use client";

import { useState } from "react";
import { featureKey, type Feature, type OwnFeature, type SubscribedFeature } from "../lib/core";

type FeaturesProps = {
  features: Feature[];
  ready: boolean;
  error: string;
  pending: string;
  onToggle: (feature: Feature) => void;
  onUndo: (name: string) => void;
  onDelete: (feature: Feature) => void;
  onUpdate: (feature: SubscribedFeature) => void;
  onPublish: (name: string, title: string, description: string) => Promise<void>;
  onUnpublish: (feature: OwnFeature) => void;
};

function isOwn(feature: Feature): feature is OwnFeature {
  return feature.source === "own";
}

export function Features({
  features,
  ready,
  error,
  pending,
  onToggle,
  onUndo,
  onDelete,
  onUpdate,
  onPublish,
  onUnpublish,
}: FeaturesProps) {
  const [publishName, setPublishName] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [publishError, setPublishError] = useState("");
  const locked = pending !== "";

  function openPublish(name: string) {
    setPublishName(name);
    setTitle("");
    setDescription("");
    setPublishError("");
  }

  async function submitPublish(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextTitle = title.trim();
    const nextDescription = description.trim();
    if (!publishName || !nextTitle || !nextDescription) return;
    setPublishError("");
    try {
      await onPublish(publishName, nextTitle, nextDescription);
      setPublishName("");
    } catch (caught) {
      setPublishError(caught instanceof Error ? caught.message : "Could not publish this feature.");
    }
  }

  return (
    <div className="features">
      {error ? <p className="notice error">{error}</p> : null}
      {!ready && features.length === 0 && !error ? <p className="features-empty">Loading your features…</p> : null}
      {ready && features.length === 0 && !error ? (
        <p className="features-empty">No features yet. Describe a change and it will show up here.</p>
      ) : null}
      {features.length > 0 ? (
        <ul>
          {features.map((feature) => {
            const key = featureKey(feature);
            return (
              <li key={key} aria-busy={pending === key}>
                <label className="switch">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={feature.enabled}
                    disabled={locked}
                    onChange={() => onToggle(feature)}
                  />
                  {isOwn(feature) ? (
                    <span className="feature-text">
                      <span className="feature-name" title={feature.name}>
                        {feature.name}
                      </span>
                      <code title={feature.route}>{feature.route}</code>
                      {feature.published ? (
                        <span className="feature-published">
                          Published{feature.published_version === null ? "" : ` v${feature.published_version}`}
                        </span>
                      ) : null}
                    </span>
                  ) : (
                    <span className="feature-text">
                      <span className="feature-name" title={feature.title || feature.name}>
                        {feature.title || feature.name}
                      </span>
                      <span className="feature-sub">
                        <code title={feature.name}>{feature.name}</code>
                        <span>{feature.owner}</span>
                        <span>v{feature.version}</span>
                        {feature.published ? null : <span>Unpublished</span>}
                      </span>
                      <code title={feature.route}>{feature.route}</code>
                    </span>
                  )}
                  <span className={feature.enabled ? "feature-state is-on" : "feature-state is-off"}>
                    {feature.enabled ? "On" : "Off"}
                  </span>
                </label>
                <span className="feature-actions">
                  {isOwn(feature) ? (
                    <>
                      {publishName === feature.name ? null : (
                        <button
                          type="button"
                          className="ghost"
                          aria-label={`Publish ${feature.name}`}
                          disabled={locked}
                          onClick={() => openPublish(feature.name)}
                        >
                          Publish
                        </button>
                      )}
                      {feature.published && feature.feature_id ? (
                        <button
                          type="button"
                          className="ghost"
                          aria-label={`Unpublish ${feature.name}`}
                          disabled={locked}
                          onClick={() => onUnpublish(feature)}
                        >
                          Unpublish
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="undo"
                        aria-label={`Undo ${feature.name}`}
                        disabled={locked}
                        onClick={() => onUndo(feature.name)}
                      >
                        Undo
                      </button>
                      <button
                        type="button"
                        className="delete"
                        aria-label={`Delete ${feature.name}`}
                        disabled={locked}
                        onClick={() => onDelete(feature)}
                      >
                        Delete
                      </button>
                    </>
                  ) : (
                    <>
                      {feature.update_available ? (
                        <button
                          type="button"
                          className="update"
                          aria-label={`Update ${feature.title || feature.name}`}
                          title={`Version ${feature.latest_version} is available`}
                          disabled={locked}
                          onClick={() => onUpdate(feature)}
                        >
                          Update
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="delete"
                        aria-label={`Unsubscribe from ${feature.title || feature.name}`}
                        disabled={locked}
                        onClick={() => onDelete(feature)}
                      >
                        Unsubscribe
                      </button>
                    </>
                  )}
                </span>
                {isOwn(feature) && publishName === feature.name ? (
                  <form className="feature-form" onSubmit={(event) => void submitPublish(event)}>
                    <label>
                      Title
                      <input
                        value={title}
                        required
                        disabled={locked}
                        autoComplete="off"
                        onChange={(event) => setTitle(event.target.value)}
                      />
                    </label>
                    <label>
                      Description
                      <textarea
                        value={description}
                        required
                        rows={3}
                        disabled={locked}
                        onChange={(event) => setDescription(event.target.value)}
                      />
                    </label>
                    <p className="feature-hint">You can also ask in the chat.</p>
                    {publishError ? <p className="notice error">{publishError}</p> : null}
                    <span className="feature-actions">
                      <button
                        type="submit"
                        className="ghost"
                        disabled={locked || title.trim() === "" || description.trim() === ""}
                      >
                        Publish
                      </button>
                      <button
                        type="button"
                        className="ghost"
                        disabled={locked}
                        onClick={() => setPublishName("")}
                      >
                        Cancel
                      </button>
                    </span>
                  </form>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
