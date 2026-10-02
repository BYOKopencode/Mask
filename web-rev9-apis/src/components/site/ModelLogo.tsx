import { memo } from "react";

import { familyOf } from "@/lib/models";
import { cn } from "@/lib/utils";

interface ModelLogoProps {
  modelId: string;
  size?: number;
  className?: string;
}

/** Company logo for a model id; neutral lettermark when no logo exists. */
export const ModelLogo = memo(function ModelLogo({ modelId, size = 28, className }: ModelLogoProps) {
  const family = familyOf(modelId);
  const inner = Math.round(size * 0.62);
  return (
    <span
      className={cn("inline-flex shrink-0 items-center justify-center rounded-lg border border-border bg-raised", className)}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {family.logo ? (
        <img
          src={family.logo}
          alt=""
          width={inner}
          height={inner}
          loading="lazy"
          className={cn(family.key === "openai" || family.key === "xai" ? "invert" : "")}
        />
      ) : (
        <span className="mono text-[11px] font-semibold" style={{ color: family.tint, fontSize: Math.max(10, size * 0.4) }}>
          {family.mark}
        </span>
      )}
    </span>
  );
});

interface FamilyLogoProps {
  logo: string | null;
  mark: string;
  tint: string;
  invert?: boolean;
  size?: number;
}

export const FamilyLogo = memo(function FamilyLogo({ logo, mark, tint, invert, size = 28 }: FamilyLogoProps) {
  if (!logo) {
    return (
      <span className="mono inline-flex items-center justify-center font-bold" style={{ width: size, height: size, color: tint, fontSize: size * 0.7 }}>
        {mark}
      </span>
    );
  }
  return <img src={logo} alt="" width={size} height={size} className={invert ? "invert" : ""} aria-hidden="true" />;
});
