import { Camera } from "lucide-react";

interface UserProfileProps {
  name: string;
  email: string;
  editable?: boolean;
}

export default function UserProfile({ name, email, editable = false }: UserProfileProps) {
  const initials = name
    .split(" ")
    .map((n) => n[0])
    .join("");

  return (
    <div className="flex items-center gap-4">
      <div className="relative">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-primary-dim text-lg font-semibold text-primary">
          {initials}
        </div>
        {editable && (
          <button
            className="absolute -bottom-1 -right-1 flex h-6 w-6 items-center justify-center rounded-full border-2 border-surface bg-primary text-white"
            aria-label="Change photo"
          >
            <Camera size={11} />
          </button>
        )}
      </div>
      <div>
        <div className="text-[15px] font-semibold text-text">{name}</div>
        <div className="text-[13px] text-text-muted">{email}</div>
      </div>
    </div>
  );
}
