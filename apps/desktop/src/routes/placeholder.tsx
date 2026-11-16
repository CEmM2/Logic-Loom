interface Props {
  title: string;
  subtitle?: string;
}

export function Placeholder({ title, subtitle }: Props) {
  return (
    <main className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {subtitle && (
        <p className="text-sm text-muted-foreground max-w-md">{subtitle}</p>
      )}
    </main>
  );
}
