type ResizableNativeImage<TImage> = {
  isEmpty(): boolean;
  getSize(): { width: number; height: number };
  resize(options: { width: number; height: number }): TImage;
};

type NativeImageFactory<TImage extends ResizableNativeImage<TImage>> = {
  createFromPath(iconPath: string): TImage;
};

type CreateTrayIconOptions<TImage extends ResizableNativeImage<TImage>> = {
  iconPath: string;
  nativeImage: NativeImageFactory<TImage>;
};

type TrayIconResult<TImage> = {
  image: TImage;
  description: string;
};

export function createTrayIcon<TImage extends ResizableNativeImage<TImage>>({ iconPath, nativeImage }: CreateTrayIconOptions<TImage>): TrayIconResult<TImage> {
  const loadedImage = nativeImage.createFromPath(iconPath);
  const initialDescription = describeNativeImage("loaded", loadedImage);
  if (loadedImage.isEmpty()) {
    return {
      image: loadedImage,
      description: `${initialDescription}; resized=skipped`,
    };
  }

  const resizedImage = loadedImage.resize({ width: 16, height: 16 });
  return {
    image: resizedImage,
    description: `${initialDescription}; ${describeNativeImage("tray", resizedImage)}`,
  };
}

function describeNativeImage(label: string, image: { isEmpty(): boolean; getSize(): { width: number; height: number } }): string {
  const size = image.getSize();
  return `${label}: empty=${image.isEmpty()} size=${size.width}x${size.height}`;
}
