# Pro Image Converter — مبدل حرفه‌ای تصاویر

<p align="center">
  <b>یک برنامه دسکتاپ مدرن، سریع و قدرتمند برای تبدیل گروهی تصاویر با رابط کاربری زیبا (تم تیره و روشن)</b>
</p>

<p align="center">
  <a href="#نمای-برنامه--screenshots">نمای برنامه</a> •
  <a href="#امکانات--features">امکانات</a> •
  <a href="#دانلود--download">دانلود</a> •
  <a href="#نصب و اجرا--installation">نصب و اجرا</a> •
  <a href="#راهنمای-فارسی">راهنمای فارسی</a>
</p>

---

## 📸 Screenshots / نمای برنامه

| Dark Theme (حالت تیره) | Light Theme (حالت روشن) |
| :---: | :---: |
| ![Dark UI](docs/ui-dark.png) | ![Light UI](docs/ui-light.png) |

---

## 🚀 Features

- **Batch Processing**: Convert entire folders of images instantly with multi-threading (non-blocking UI).
- **Subfolder Support**: Optionally scan and process images in subdirectories while preserving folder structures.
- **Wide Format Support**:
  - **Outputs**: JPG, PNG, WEBP, TIFF, BMP, GIF, ICO, PDF
  - **Advanced Outputs**: AVIF & HEIC (when libraries are installed)
  - **Inputs**: Standard formats + SVG (via CairoSVG) + RAW camera files (DNG, CR2, CR3, NEF, ARW, RAF, etc. via rawpy).
- **Smart Image Options**:
  - Resize with aspect ratio preservation (Max Width/Height).
  - Quality adjustment for lossy formats.
  - Preserve EXIF metadata, color profiles, and file timestamps.
  - Transparent background handling for non-transparent formats (JPG/BMP/PDF background color selection).
  - Preserve animated GIFs and WebPs.
- **Modern UI**: Built with CustomTkinter featuring clean typography, instant toast notifications, progress bar, cancel operation, and auto light/dark theme adaptation.

---

## 📥 Download

You can download the latest standalone executable (`ProImageConverter.exe`) without needing to install Python:
- **[Download ProImageConverter v1.0.0 (Windows x64)](https://github.com/mr-5hady/ProImageConverter/releases/latest/download/ProImageConverter.exe)**

---

## ⚙️ Installation & Quick Start (Developer)

If you want to run from source or build the executable yourself:

### Prerequisites
- Python 3.11 or newer (make sure to check **Add Python to PATH** during installation).

### Quick Setup (Windows Batch Scripts)
1. Double-click `install.bat` (or run it in terminal) to install dependencies.
2. Run `run.bat` to launch the application.

### Manual Terminal Setup
```bash
# Clone repository
git clone https://github.com/mr-5hady/ProImageConverter.git
cd ProImageConverter

# Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Run the app
python app.py
```

### Optional Formats (SVG & RAW)
```bash
python -m pip install -r requirements-optional.txt
```

### Building Executable (.exe)
```bash
build_exe.bat
```
The standalone `.exe` will be generated inside the `dist/` folder.

---

## 🇮🇷 راهنمای فارسی

**مبدل حرفه‌ای تصاویر (Pro Image Converter)** یک نرم‌افزار دسکتاپ ویندوزی برای تبدیل دسته‌ای و سریع تصاویر است که با پایتون و CustomTkinter ساخته شده است.

### قابلیت‌های کلیدی:
- **تبدیل گروهی:** پردازش هزاران تصویر در پوشه‌های دلخواه با قابلیت حفظ ساختار زیرپوشه‌ها.
- **پشتیبانی از فرمت‌های متنوع:** JPG، PNG، WEBP، TIFF، BMP، GIF، ICO، PDF، به همراه پشتیبانی اختیاری از فایل‌های RAW دوربین‌ها و فایل‌های وکتور SVG.
- **تنظیمات پیشرفته:** تغییر اندازه هوشمند تصاویر، حفظ متادیتای EXIF، مدیریت شفافیت رنگ‌ها و حفظ انیمیشن‌های متحرک.
- **رابط کاربری مدرن:** طراحی مینیمال، شیک، با پشتیبانی کامل از حالت تیره (Dark Mode) و روشن (Light Mode) و اعلان‌های شناور بدون قفل‌شدن صفحه.

### نحوه اجرا در ویندوز:
1. پایتون ۳.۱۱ یا جدیدتر را نصب کنید (گزینه `Add Python to PATH` فراموش نشود).
2. فایل `install.bat` را اجرا کنید تا وابستگی‌ها نصب شوند.
3. فایل `run.bat` را برای اجرای برنامه بزنید.

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
