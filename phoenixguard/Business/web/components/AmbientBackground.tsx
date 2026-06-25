import { backgroundImages } from "@/lib/site-data";

export function AmbientBackground() {
  return (
    <div className="ambient-background" aria-hidden="true">
      {backgroundImages.map((image, index) => (
        <span
          className="ambient-background__slide"
          key={image}
          style={{
            backgroundImage: `url(${image})`,
            animationDelay: `${index * 9000 - 2400}ms`
          }}
        />
      ))}
      <span className="ambient-background__mesh" />
    </div>
  );
}
