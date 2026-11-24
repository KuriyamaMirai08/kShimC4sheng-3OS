/* Atlas full-page menu: fixed sidebar, page content and footer.
 *
 * Included by lvgl_port.c after the shared event/confirm helpers, so it can
 * reuse the group, keypad handling and pending-index machinery without a
 * second input path. Textures and the old row list are covered by an opaque
 * root object; only the atlas widgets receive group focus.
 */
#if defined(CONFIG_KSHIM_UI_ATLAS) && CONFIG_KSHIM_UI_ATLAS

#define ATLAS_BG       0x080d22
#define ATLAS_PANEL    0x090f26
#define ATLAS_CARD     0x0d1536
#define ATLAS_CARD_HI  0x1c2b5e
#define ATLAS_BORDER   0x9db8ff
#define ATLAS_BLUE     0x4568f5
#define ATLAS_BLUE_DIM 0x5d46d6
#define ATLAS_TEXT     0xf5f8ff
#define ATLAS_MUTED    0xc4d1f2
#define ATLAS_GREEN    0x3ddc84

#include "atlas_icons.h"

static lv_obj_t *mAtlasRoot;
static lv_obj_t *mAtlasSidebar;
static lv_obj_t *mAtlasContent;
static lv_obj_t *mAtlasStatus;
static lv_obj_t *mAtlasAutoValue;
static lv_obj_t *mAtlasLang;
static lv_obj_t *mAtlasDebug;
static const lv_font_t *mAtlasTitleFont;
static const lv_font_t *mAtlasNavFont;
static const lv_font_t *mAtlasBodyFont;
static uint8_t mAtlasBuilt;
static uint8_t mAtlasFrameProbe;
static uint32_t mAtlasFrameCount;

static int32_t AtlasW(void) { return (int32_t)mContext->LogicalWidth; }
static int32_t AtlasH(void) { return (int32_t)mContext->LogicalHeight; }


static lv_obj_t *AtlasBox(lv_obj_t *Parent, int32_t X, int32_t Y, int32_t W, int32_t H,
                          uint32_t Bg, lv_opa_t Opa, int32_t Radius, uint32_t Border,
                          int32_t BorderW)
{
  lv_obj_t *Box = lv_obj_create(Parent);
  if (Box == NULL) return NULL;
  lv_obj_set_pos(Box, X, Y);
  lv_obj_set_size(Box, W, H);
  lv_obj_set_style_bg_color(Box, lv_color_hex(Bg), 0);
  lv_obj_set_style_bg_opa(Box, Opa, 0);
  lv_obj_set_style_radius(Box, Radius, 0);
  lv_obj_set_style_border_width(Box, BorderW, 0);
  lv_obj_set_style_border_color(Box, lv_color_hex(Border), 0);
  lv_obj_set_style_pad_all(Box, 0, 0);
  lv_obj_set_style_outline_width(Box, 0, 0);
  lv_obj_set_style_shadow_width(Box, 0, 0);
  lv_obj_remove_flag(Box, LV_OBJ_FLAG_SCROLLABLE);
  return Box;
}

static lv_obj_t *AtlasText(lv_obj_t *Parent, const char *Text, const lv_font_t *Font,
                           uint32_t Color, int32_t X, int32_t Y, int32_t W, int32_t H)
{
  lv_obj_t *Label = lv_label_create(Parent);
  if (Label == NULL) return NULL;
  lv_label_set_text(Label, Text != NULL ? Text : "");
  lv_obj_set_pos(Label, X, Y);
  if (W > 0) lv_obj_set_width(Label, W);
  if (H > 0) lv_obj_set_height(Label, H);
  lv_obj_set_style_text_font(Label, Font, 0);
  lv_obj_set_style_text_color(Label, lv_color_hex(Color), 0);
  lv_obj_set_style_bg_opa(Label, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(Label, 0, 0);
  lv_obj_set_style_pad_all(Label, 0, 0);
  lv_label_set_long_mode(Label, LV_LABEL_LONG_WRAP);
  lv_obj_remove_flag(Label, LV_OBJ_FLAG_CLICKABLE);
  return Label;
}

/* Single-line label centred vertically in its parent object. */
static lv_obj_t *AtlasTextMiddle(lv_obj_t *Parent, const char *Text,
                                 const lv_font_t *Font, uint32_t Color,
                                 int32_t X, int32_t W)
{
  lv_obj_t *Label = AtlasText(Parent, Text, Font, Color, X, 0, W, 0);
  if (Label != NULL) lv_obj_align(Label, LV_ALIGN_LEFT_MID, X, 0);
  return Label;
}

static void AtlasCircle(lv_obj_t *Parent, int32_t X, int32_t Y, int32_t D,
                        uint32_t Color, int32_t BorderW)
{
  lv_obj_t *C = lv_obj_create(Parent);
  if (C == NULL) return;
  lv_obj_set_pos(C, X, Y);
  lv_obj_set_size(C, D, D);
  lv_obj_set_style_radius(C, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(C, BorderW > 0 ? LV_OPA_TRANSP : LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(C, lv_color_hex(Color), 0);
  lv_obj_set_style_border_width(C, BorderW, 0);
  lv_obj_set_style_border_color(C, lv_color_hex(Color), 0);
  lv_obj_set_style_pad_all(C, 0, 0);
  lv_obj_remove_flag(C, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_remove_flag(C, LV_OBJ_FLAG_SCROLLABLE);
}

static void AtlasLine(lv_obj_t *Parent, lv_point_precise_t *Points, uint32_t Count,
                      uint32_t Color, int32_t Width)
{
  lv_obj_t *Line = lv_line_create(Parent);
  if (Line == NULL) return;
  lv_line_set_points(Line, Points, Count);
  lv_obj_set_style_line_color(Line, lv_color_hex(Color), 0);
  lv_obj_set_style_line_width(Line, Width, 0);
  lv_obj_set_style_line_rounded(Line, true, 0);
  lv_obj_remove_flag(Line, LV_OBJ_FLAG_CLICKABLE);
}

/* Selected option badge: filled accent circle with a white check mark. */
static void AtlasCheckBadge(lv_obj_t *Parent, int32_t X, int32_t Y, int32_t D)
{
  AtlasCircle(Parent, X, Y, D, ATLAS_BLUE, 0);
  lv_point_precise_t Check[3] = {
    {X + D * 28 / 100, Y + D * 52 / 100},
    {X + D * 44 / 100, Y + D * 70 / 100},
    {X + D * 74 / 100, Y + D * 34 / 100},
  };
  AtlasLine(Parent, Check, 3, 0xffffff, 4);
}

/* Bitmap line icons live in atlas_icons.h: 64x64 A8 descriptors that are
 * recoloured at runtime, so focus and selection share one asset. */
static void AtlasIcon(lv_obj_t *Parent, kshim_menu_role_t Role, int32_t X, int32_t Y,
                      int32_t Size, uint32_t Color)
{
  const lv_image_dsc_t *Asset = AtlasIconAsset(Role);
  if (Asset == NULL || Size <= 0) return;
  lv_obj_t *Icon = lv_image_create(Parent);
  if (Icon == NULL) return;
  lv_image_set_src(Icon, Asset);
  lv_obj_set_pos(Icon, X, Y);
  (void)Size;
  lv_obj_set_style_img_recolor(Icon, lv_color_hex(Color), 0);
  lv_obj_set_style_img_recolor_opa(Icon, LV_OPA_COVER, 0);
  lv_obj_remove_flag(Icon, LV_OBJ_FLAG_CLICKABLE);
}

static const char *AtlasNavName(size_t Index)
{
  switch (Index) {
  case 0: return KSHIM_MENU_LABEL_ANDROID;
  case 1:
    return (mContext != NULL && mContext->UiPage == 0U && mContext->EntryCount > 1U &&
            mContext->Entries[1] != NULL && mContext->Entries[1][0])
        ? mContext->Entries[1]
        : "iPadOS 27";
  case 2: return "Linux";
  case 3: return KSHIM_MENU_LABEL_ADVANCED;
  case 4: return KSHIM_MENU_LABEL_SETTINGS;
  case 5: return kshim_menu_text(KSHIM_TEXT_INFO_LABEL);
  case 6: return KSHIM_MENU_LABEL_RESTART;
  case 7: return KSHIM_MENU_LABEL_OFF;
  default: return "";
  }
}

static kshim_menu_role_t AtlasNavRole(size_t Index)
{
  static const kshim_menu_role_t Roles[8] = {
    KSHIM_MENU_ROLE_ANDROID, KSHIM_MENU_ROLE_INTERNAL, KSHIM_MENU_ROLE_TF,
    KSHIM_MENU_ROLE_ADVANCED, KSHIM_MENU_ROLE_SETTINGS,
    KSHIM_MENU_ROLE_DEVICE_INFO, KSHIM_MENU_ROLE_RESTART, KSHIM_MENU_ROLE_OFF,
  };
  return Index < 8 ? Roles[Index] : KSHIM_MENU_ROLE_UNKNOWN;
}

static int AtlasParentNav(uint8_t Page)
{
  switch (Page) {
  case 1: return 3;
  case 2: case 4: case 5: case 6: case 7: return 4;
  case 3: return 5;
  default: return -1;
  }
}

static int AtlasFindBack(void)
{
  if (mContext == NULL) return -1;
  if (mContext->UiPage == 3U && mContext->InitialIndex < mContext->EntryCount)
    return (int)mContext->InitialIndex;
  for (size_t i = 0; i < mContext->EntryCount; ++i) {
    if (mContext->Roles[i] == KSHIM_MENU_ROLE_BACK) return (int)i;
    if (mContext->ActionLabels[i] != NULL &&
        strcmp(mContext->ActionLabels[i], KSHIM_MENU_ACTION_BACK) == 0)
      return (int)i;
  }
  return -1;
}

static void AtlasRowFocused(lv_event_t *Event)
{
  lv_obj_t *Row = lv_event_get_target_obj(Event);
  lv_event_code_t Code = lv_event_get_code(Event);
  if (Row == NULL) return;
  if (Code == LV_EVENT_FOCUSED) lv_obj_add_state(Row, LV_STATE_CHECKED);
  else if (Code == LV_EVENT_DEFOCUSED) lv_obj_remove_state(Row, LV_STATE_CHECKED);
}

static void AtlasRowClicked(lv_event_t *Event)
{
  if (Event == NULL || mContext == NULL || lv_event_get_code(Event) != LV_EVENT_CLICKED)
    return;
  lv_indev_t *Input = lv_event_get_indev(Event);
  if (Input != NULL && lv_indev_get_type(Input) == LV_INDEV_TYPE_POINTER) return;
  int Index = (int)(intptr_t)lv_event_get_user_data(Event);
  if (Index >= 0 && (size_t)Index < mContext->EntryCount)
    KshimConfirmIndex((size_t)Index);
}

static lv_obj_t *AtlasRow(lv_obj_t *Parent, int32_t X, int32_t Y, int32_t W, int32_t H,
                          int Index, kshim_menu_role_t Role)
{
  lv_obj_t *Row = AtlasBox(Parent, X, Y, W, H, ATLAS_CARD, LV_OPA_30, 18,
                           ATLAS_BORDER, 1);
  if (Row == NULL) return NULL;
  lv_obj_set_style_border_opa(Row, LV_OPA_20, 0);
  lv_obj_add_flag(Row, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_style_bg_color(Row, lv_color_hex(ATLAS_BLUE), LV_STATE_FOCUS_KEY);
  lv_obj_set_style_bg_grad_color(Row, lv_color_hex(ATLAS_BLUE_DIM), LV_STATE_FOCUS_KEY);
  lv_obj_set_style_bg_grad_dir(Row, LV_GRAD_DIR_VER, LV_STATE_FOCUS_KEY);
  lv_obj_set_style_bg_opa(Row, LV_OPA_70, LV_STATE_FOCUS_KEY);
  lv_obj_set_style_border_color(Row, lv_color_hex(0xc8d8ff), LV_STATE_FOCUS_KEY);
  lv_obj_set_style_border_opa(Row, LV_OPA_60, LV_STATE_FOCUS_KEY);
  lv_obj_set_style_border_width(Row, 2, LV_STATE_FOCUS_KEY);
  lv_obj_set_style_bg_color(Row, lv_color_hex(ATLAS_BLUE), LV_STATE_CHECKED);
  lv_obj_set_style_bg_grad_color(Row, lv_color_hex(ATLAS_BLUE_DIM), LV_STATE_CHECKED);
  lv_obj_set_style_bg_grad_dir(Row, LV_GRAD_DIR_VER, LV_STATE_CHECKED);
  lv_obj_set_style_bg_opa(Row, LV_OPA_70, LV_STATE_CHECKED);
  lv_obj_set_style_border_color(Row, lv_color_hex(0xc8d8ff), LV_STATE_CHECKED);
  lv_obj_set_style_border_opa(Row, LV_OPA_60, LV_STATE_CHECKED);
  lv_obj_set_style_border_width(Row, 2, LV_STATE_CHECKED);
  if (Role != KSHIM_MENU_ROLE_UNKNOWN) AtlasIcon(Row, Role, 27, (H - 56) / 2 + 2, 56, ATLAS_BLUE);
  if (Index >= 0) {
    lv_obj_add_event_cb(Row, AtlasRowFocused, LV_EVENT_ALL, NULL);
    lv_obj_add_event_cb(Row, AtlasRowClicked, LV_EVENT_CLICKED, (void *)(intptr_t)Index);
    lv_group_add_obj((lv_group_t *)mContext->Group, Row);
  }
  return Row;
}

static void AtlasContentTitle(const char *Title, int32_t Height)
{
  AtlasBox(mAtlasContent, 0, 0, 8, Height, ATLAS_BLUE, LV_OPA_COVER, 4, ATLAS_BLUE, 0);
  AtlasText(mAtlasContent, Title, mAtlasNavFont, ATLAS_TEXT, 28, 0, 1200, Height);
}

static void AtlasBuildNav(void)
{
  const int32_t W = AtlasW();
  const int32_t Margin = W * 21 / 1000;
  const int32_t SideW = W * 250 / 1000;
  const int32_t InnerW = SideW - 34;
  const int32_t RowH = 104;
  int Parent = AtlasParentNav(mContext->UiPage);
  for (size_t i = 0; i < 8; ++i) {
    int32_t Y = (int32_t)i * (RowH + 12);
    lv_obj_t *Row = AtlasBox(mAtlasSidebar, 0, Y, InnerW, RowH, ATLAS_CARD,
                             LV_OPA_30, 18, ATLAS_BORDER, 1);
    if (Row == NULL) continue;
    lv_obj_set_style_border_opa(Row, LV_OPA_20, 0);
    lv_obj_set_style_bg_color(Row, lv_color_hex(ATLAS_BLUE), LV_STATE_CHECKED);
    lv_obj_set_style_bg_grad_color(Row, lv_color_hex(ATLAS_BLUE_DIM), LV_STATE_CHECKED);
    lv_obj_set_style_bg_grad_dir(Row, LV_GRAD_DIR_VER, LV_STATE_CHECKED);
    lv_obj_set_style_bg_opa(Row, LV_OPA_70, LV_STATE_CHECKED);
    lv_obj_set_style_border_width(Row, 2, LV_STATE_CHECKED);
    lv_obj_set_style_border_color(Row, lv_color_hex(0xc8d8ff), LV_STATE_CHECKED);
    lv_obj_set_style_border_opa(Row, LV_OPA_60, LV_STATE_CHECKED);
    if (mContext->UiPage == 0U) {
      lv_obj_add_flag(Row, LV_OBJ_FLAG_CLICKABLE);
      lv_obj_add_event_cb(Row, AtlasRowFocused, LV_EVENT_ALL, NULL);
      lv_obj_add_event_cb(Row, AtlasRowClicked, LV_EVENT_CLICKED, (void *)(intptr_t)i);
      if ((int)i == Parent) lv_obj_add_state(Row, LV_STATE_CHECKED);
      lv_group_add_obj((lv_group_t *)mContext->Group, Row);
    } else if ((int)i == Parent) {
      lv_obj_add_state(Row, LV_STATE_CHECKED);
    }
    uint32_t IconColor = (i == 0U) ? ATLAS_GREEN
                       : (i == 1U || i == 2U) ? ATLAS_TEXT
                       : ((mContext->UiPage != 0U && (int)i == Parent) ? ATLAS_TEXT : 0xd2ddf8);
    AtlasIcon(Row, AtlasNavRole(i), Margin / 2 - 14, (RowH - 56) / 2, 56, IconColor);
    AtlasTextMiddle(Row, AtlasNavName(i), mAtlasNavFont, ATLAS_TEXT, 86, InnerW - 102);
  }
}

static void AtlasBuildEnvironment(void)
{
  const int32_t W = AtlasW() * 62 / 100;
  const int32_t LabelW = W / 2 - 80;
  const int32_t Col2 = W / 2;
  const int32_t RowY[2] = {120, 360};
  char Auto[32];
  unsigned Seconds = mContext->CountdownMs / 1000U;
  if (mContext->CountdownCancelled != 0U)
    snprintf(Auto, sizeof(Auto), "%s", kshim_menu_text(KSHIM_TEXT_ENV_CANCELLED));
  else if (mContext->CountdownMs == 0U)
    snprintf(Auto, sizeof(Auto), "%s", kshim_menu_text(KSHIM_TEXT_ENV_OFF));
  else
    snprintf(Auto, sizeof(Auto), "%u 秒", Seconds);

  AtlasContentTitle(kshim_menu_text(KSHIM_TEXT_SECTION_ENV), 52);
  const char *Labels[4] = {
    kshim_menu_text(KSHIM_TEXT_ENV_SLOT), kshim_menu_text(KSHIM_TEXT_ENV_DEFAULT),
    kshim_menu_text(KSHIM_TEXT_ENV_TIMEOUT), kshim_menu_text(KSHIM_TEXT_ENV_LAST),
  };
  const char *Values[4] = {
    (mContext->UiEnvSlot != NULL && mContext->UiEnvSlot[0]) ? mContext->UiEnvSlot : "A",
    (mContext->UiEnvDefault != NULL && mContext->UiEnvDefault[0]) ? mContext->UiEnvDefault :
      (mContext->EntryCount > mContext->InitialIndex ? mContext->Entries[mContext->InitialIndex] : "-"),
    Auto,
    (mContext->UiEnvLast != NULL && mContext->UiEnvLast[0]) ? mContext->UiEnvLast : "-",
  };
  for (int i = 0; i < 4; ++i) {
    int32_t X = (i & 1) ? Col2 : 0;
    int32_t Y = RowY[i >> 1];
    AtlasText(mAtlasContent, Labels[i], mAtlasBodyFont, ATLAS_MUTED, X, Y, LabelW, 34);
    lv_obj_t *ValueLabel = AtlasText(mAtlasContent, Values[i], mAtlasNavFont, ATLAS_TEXT,
                                     X, Y + 44, LabelW, 56);
    if (i == 2) mAtlasAutoValue = ValueLabel;
  }
}

static void AtlasCardRow(int32_t Y, int32_t W, size_t Index, const char *Title,
                         const char *Value, const char *Desc)
{
  const int32_t H = 150;
  lv_obj_t *Row = AtlasRow(mAtlasContent, 0, Y, W, H, (int)Index, mContext->Roles[Index]);
  if (Row == NULL) return;
  lv_obj_t *TitleLabel = AtlasText(Row, Title, mAtlasNavFont, ATLAS_TEXT, 104, 24, W - 360, 46);
  if (TitleLabel != NULL) lv_label_set_long_mode(TitleLabel, LV_LABEL_LONG_DOT);
  if (Value != NULL && Value[0]) {
    lv_obj_t *ValueLabel = AtlasText(Row, Value, mAtlasBodyFont, ATLAS_BLUE,
                                     W - 300, 32, 220, 40);
    if (ValueLabel != NULL)
      lv_obj_set_style_text_align(ValueLabel, LV_TEXT_ALIGN_RIGHT, 0);
  }
  lv_obj_t *DescLabel = AtlasText(Row, Desc != NULL ? Desc : "", mAtlasBodyFont, ATLAS_MUTED,
                                  104, 80, W - 200, 54);
  if (DescLabel != NULL) lv_label_set_long_mode(DescLabel, LV_LABEL_LONG_DOT);
  AtlasText(Row, ">", mAtlasNavFont, ATLAS_MUTED, W - 64, 44, 40, 52);
}

static void AtlasBuildAdvanced(void)
{
  int32_t W = AtlasW() * 62 / 100;
  AtlasContentTitle(kshim_menu_text(KSHIM_TEXT_ADVANCED), 52);
  int32_t Y = 96;
  for (size_t i = 0; i < mContext->EntryCount; ++i) {
    if (mContext->Roles[i] == KSHIM_MENU_ROLE_BACK ||
        (mContext->ActionLabels[i] != NULL &&
         strcmp(mContext->ActionLabels[i], KSHIM_MENU_ACTION_BACK) == 0))
      continue;
    AtlasCardRow(Y, W, i, mContext->Entries[i], NULL, mContext->Descriptions[i]);
    Y += 170;
  }
  AtlasText(mAtlasContent, kshim_menu_text(KSHIM_TEXT_ADVANCED_NOTE), mAtlasBodyFont,
            ATLAS_MUTED, 0, Y + 10, W, 60);
}

static void AtlasBuildSettings(void)
{
  int32_t W = AtlasW() * 62 / 100;
  AtlasContentTitle(kshim_menu_text(KSHIM_TEXT_SETTINGS_LABEL), 52);
  int32_t Y = 96;
  for (size_t i = 0; i < mContext->EntryCount; ++i) {
    if (mContext->Roles[i] == KSHIM_MENU_ROLE_BACK ||
        (mContext->ActionLabels[i] != NULL &&
         strcmp(mContext->ActionLabels[i], KSHIM_MENU_ACTION_BACK) == 0))
      continue;
    const char *Title = mContext->Entries[i];
    const char *Value = "";
    if (mContext->Roles[i] == KSHIM_MENU_ROLE_LANGUAGE) {
      Title = kshim_menu_text(KSHIM_TEXT_LANGUAGE_LABEL);
      Value = kshim_menu_text(KSHIM_TEXT_LANGUAGE_STATE);
    } else if (mContext->Roles[i] == KSHIM_MENU_ROLE_SETTINGS) {
      Title = kshim_menu_text(KSHIM_TEXT_BOOT_DELAY_LABEL);
      Value = mContext->Entries[i];
      const char *Sep = strstr(Value, "\xef\xbc\x9a"); /* full-width colon */
      if (Sep != NULL) {
        const char *Tail = Sep + 3;
        while (*Tail == ' ') Tail++;
        Value = Tail;
      }
    } else if (mContext->Roles[i] == KSHIM_MENU_ROLE_DEVICE) {
      Title = kshim_menu_text(KSHIM_TEXT_DEVICE_LABEL);
      Value = mContext->Entries[i];
    }
    AtlasCardRow(Y, W, i, Title, Value, mContext->Descriptions[i]);
    Y += 170;
  }
  AtlasText(mAtlasContent, kshim_menu_text(KSHIM_TEXT_SETTINGS_DESC), mAtlasBodyFont,
            ATLAS_MUTED, 0, Y + 10, W, 60);
}

static void AtlasBuildLanguage(void)
{
  int32_t W = AtlasW() * 62 / 100;
  AtlasContentTitle(kshim_menu_text(KSHIM_TEXT_LANGUAGE_LABEL), 52);
  int32_t CardW = (W - 40) / 2;
  size_t Row = 0;
  for (size_t i = 0; i < mContext->EntryCount; ++i) {
    if (mContext->Roles[i] == KSHIM_MENU_ROLE_BACK ||
        (mContext->ActionLabels[i] != NULL &&
         strcmp(mContext->ActionLabels[i], KSHIM_MENU_ACTION_BACK) == 0))
      continue;
    /* The language cards are text-only: drawing the language glyph here
     * overlaps the title/description at this card size. */
    lv_obj_t *Card = AtlasRow(mAtlasContent, (int32_t)Row * (CardW + 40), 96,
                              CardW, 190, (int)i, KSHIM_MENU_ROLE_UNKNOWN);
    if (Card == NULL) continue;
    AtlasText(Card, mContext->Entries[i], mAtlasNavFont, ATLAS_TEXT, 36, 34, CardW - 120, 52);
    AtlasText(Card, mContext->Descriptions[i], mAtlasBodyFont, ATLAS_MUTED,
              36, 100, CardW - 90, 60);
    if (mContext->Roles[i] == KSHIM_MENU_ROLE_LANG_ZH &&
        kshim_menu_get_language() == KSHIM_MENU_LANG_ZH_CN) {
      lv_obj_add_state(Card, LV_STATE_CHECKED);
      AtlasCheckBadge(Card, CardW - 62, 22, 34);
    } else if (mContext->Roles[i] == KSHIM_MENU_ROLE_LANG_EN &&
               kshim_menu_get_language() == KSHIM_MENU_LANG_EN) {
      lv_obj_add_state(Card, LV_STATE_CHECKED);
      AtlasCheckBadge(Card, CardW - 62, 22, 34);
    }
    Row++;
  }
  AtlasText(mAtlasContent, kshim_menu_text(KSHIM_TEXT_LANGUAGE_DESC), mAtlasBodyFont,
            ATLAS_MUTED, 0, 360, W, 60);
}

static unsigned AtlasEntrySeconds(const char *Name)
{
  if (Name == NULL) return 0;
  while (*Name && (*Name < '0' || *Name > '9')) Name++;
  unsigned Value = 0;
  while (*Name >= '0' && *Name <= '9') { Value = Value * 10U + (unsigned)(*Name - '0'); Name++; }
  return Value;
}

static void AtlasBuildDelay(void)
{
  int32_t W = AtlasW() * 62 / 100;
  AtlasContentTitle(kshim_menu_text(KSHIM_TEXT_BOOT_DELAY_LABEL), 52);
  int32_t BtnW = (W - 60) / 3;
  int32_t Y = 96;
  size_t Col = 0;
  unsigned Current = mContext->CountdownMs / 1000U;
  for (size_t i = 0; i < mContext->EntryCount; ++i) {
    if (mContext->Roles[i] == KSHIM_MENU_ROLE_BACK ||
        (mContext->ActionLabels[i] != NULL &&
         strcmp(mContext->ActionLabels[i], KSHIM_MENU_ACTION_BACK) == 0))
      continue;
    lv_obj_t *Btn = AtlasRow(mAtlasContent, (int32_t)Col * (BtnW + 30), Y, BtnW, 100,
                             (int)i, KSHIM_MENU_ROLE_SETTINGS);
    if (Btn == NULL) continue;
    AtlasText(Btn, mContext->Entries[i], mAtlasNavFont, ATLAS_TEXT, 26, 0, BtnW - 52, 100);
    unsigned Sec = AtlasEntrySeconds(mContext->Entries[i]);
    bool Off = mContext->CountdownMs == 0U && mContext->CountdownCancelled == 0U &&
        strcmp(mContext->Entries[i], kshim_menu_text(KSHIM_TEXT_ENV_OFF)) == 0;
    if ((mContext->CountdownMs != 0U && Sec == Current) ||
        (mContext->CountdownMs == 0U && Off))
      lv_obj_add_state(Btn, LV_STATE_CHECKED);
    Col++;
    if (Col == 3U) { Col = 0; Y += 120; }
  }
  AtlasText(mAtlasContent, kshim_menu_text(KSHIM_TEXT_BOOT_DELAY_DESC), mAtlasBodyFont,
            ATLAS_MUTED, 0, Y + 140, W, 80);
}

static void AtlasBuildInfo(void)
{
  int32_t W = AtlasW() * 62 / 100;
  AtlasContentTitle(kshim_menu_text(KSHIM_TEXT_INFO_LABEL), 52);
  static const kshim_menu_text_id Labels[7] = {
    KSHIM_TEXT_INFO_BOARD, KSHIM_TEXT_INFO_SOC, KSHIM_TEXT_INFO_MODEL,
    KSHIM_TEXT_INFO_COMPATIBLE, KSHIM_TEXT_INFO_RAM,
    KSHIM_TEXT_INFO_FRAMEBUFFER, KSHIM_TEXT_INFO_VERSION,
  };
  const int32_t CellW = (W - 60) / 2;
  for (size_t i = 0; i < 7 && i < mContext->UiInfoCount; ++i) {
    int32_t X = (int32_t)(i & 1U) * (CellW + 60);
    int32_t Y = 96 + (int32_t)(i >> 1) * 150;
    AtlasText(mAtlasContent, kshim_menu_text(Labels[i]), mAtlasBodyFont, ATLAS_MUTED,
              X, Y, CellW, 34);
    AtlasText(mAtlasContent,
              (mContext->UiInfoValues != NULL && mContext->UiInfoValues[i] != NULL)
                  ? mContext->UiInfoValues[i] : "-",
              mAtlasBodyFont, ATLAS_TEXT, X, Y + 44, CellW, 64);
  }
  AtlasText(mAtlasContent, kshim_menu_text(KSHIM_TEXT_INFO_NOTE), mAtlasBodyFont,
            ATLAS_MUTED, 0, 680, W, 50);
}

static void AtlasBuildGeneric(void)
{
  int32_t W = AtlasW() * 62 / 100;
  AtlasContentTitle(kshim_menu_text(KSHIM_TEXT_SECTION), 52);
  int32_t Y = 96;
  for (size_t i = 0; i < mContext->EntryCount; ++i) {
    AtlasCardRow(Y, W, i, mContext->Entries[i], NULL, mContext->Descriptions[i]);
    Y += 120;
  }
}

static void AtlasBuildBack(void)
{
  int Back = AtlasFindBack();
  if (Back < 0) return;
  /* Fixed page slots: computing from the last child proved unreliable while
   * the page was being rebuilt (the back row could land on top of the title
   * before the cards were laid out). Keep the button below each page's body. */
  int32_t Y;
  switch (mContext->UiPage) {
  case 1: Y = 1010; break; /* Advanced: three cards + note */
  case 2: Y = 700;  break; /* Language: two big cards */
  case 3: Y = 760;  break; /* Device info: four rows + note */
  case 4: Y = 520;  break; /* Settings: two rows */
  case 7: Y = 640;  break; /* Boot delay: two rows of buttons */
  default: Y = 1480; break;
  }
  int32_t MaxY = lv_obj_get_height(mAtlasContent) - 90;
  if (Y > MaxY) Y = MaxY;
  lv_obj_t *Btn = AtlasRow(mAtlasContent, 0, Y, 260, 70, Back, KSHIM_MENU_ROLE_BACK);
  if (Btn == NULL) return;
  const char *Label = mContext->Entries[Back];
  if (mContext->UiPage == 3U) Label = KSHIM_MENU_LABEL_BACK;
  AtlasTextMiddle(Btn, Label, mAtlasBodyFont, ATLAS_TEXT, 92, 150);
}

/* Footer status line is rebuilt on every page switch, not only when the
 * countdown second changes; otherwise a cancelled/restarted countdown can
 * leave stale text on a newly built page. */
static void AtlasRefreshStatus(void)
{
  if (mContext == NULL || mAtlasStatus == NULL) return;
  char Text[192];
  if (mContext->CountdownMs == 0U) {
    snprintf(Text, sizeof(Text), "%s", kshim_menu_text(
        mContext->CountdownCancelled != 0U ? KSHIM_TEXT_ENV_CANCELLED : KSHIM_TEXT_ENV_OFF));
  } else {
    const char *Name = (mContext->InitialIndex < mContext->EntryCount)
        ? mContext->Entries[mContext->InitialIndex] : "";
    snprintf(Text, sizeof(Text), "%u 秒后自动启动 %s",
             (unsigned)(mContext->CountdownMs / 1000U), Name);
  }
  lv_label_set_text(mAtlasStatus, Text);
}

static void AtlasRebuild(size_t Initial)
{
  if (!mAtlasBuilt || mAtlasContent == NULL || mContext == NULL) return;
  lv_group_t *Group = (lv_group_t *)mContext->Group;
  /* The auto-start label only exists on the main page. Drop the pointer
   * before deleting the content tree: set_countdown() runs after every page
   * switch and must not write through a dangling label. */
  mAtlasAutoValue = NULL;
  if (Group != NULL) lv_group_remove_all_objs(Group);
  lv_obj_clean(mAtlasSidebar);
  lv_obj_clean(mAtlasContent);

  AtlasBuildNav();
  switch (mContext->UiPage) {
  case 1: AtlasBuildAdvanced(); break;
  case 2: AtlasBuildLanguage(); break;
  case 3: AtlasBuildInfo(); break;
  case 4: AtlasBuildSettings(); break;
  case 7: AtlasBuildDelay(); break;
  case 0: AtlasBuildEnvironment(); break;
  default: AtlasBuildGeneric(); break;
  }
  if (mContext->UiPage != 0U) AtlasBuildBack();

  if (Group != NULL) {
    uint32_t Count = lv_group_get_obj_count(Group);
    if (Count > 0U) {
      lv_obj_t *Focus = NULL;
      if (mContext->UiPage == 0U && Initial < Count)
        Focus = lv_group_get_obj_by_index(Group, (uint32_t)Initial);
      else
        Focus = lv_group_get_obj_by_index(Group, 0U);
      if (Focus != NULL) {
        lv_group_focus_obj(Focus);
        lv_obj_add_state(Focus, LV_STATE_CHECKED);
      }
    }
  }
  AtlasRefreshStatus();
  lv_obj_invalidate(lv_screen_active());
  mAtlasFrameProbe = 1U;
}

static int KshimAtlasInit(void)
{
  if (mAtlasBuilt) return 0;
  if (mContext == NULL) return -1;
  mAtlasTitleFont = mTitleFont != NULL ? mTitleFont : &lv_font_montserrat_20;
  mAtlasNavFont = mRowFont != NULL ? mRowFont : &lv_font_montserrat_20;
  mAtlasBodyFont = mStatusFont != NULL ? mStatusFont : &lv_font_montserrat_20;

  lv_obj_t *Screen = lv_screen_active();
  const int32_t W = AtlasW(), H = AtlasH();
  const int32_t Margin = W * 21 / 1000;
  const int32_t HeaderH = H * 74 / 1000;
  const int32_t FooterH = H * 46 / 1000;
  const int32_t SideW = W * 250 / 1000;
  const int32_t ContentX = Margin + SideW + W * 30 / 1000;
  const int32_t ContentW = W - ContentX - Margin;

  mAtlasRoot = AtlasBox(Screen, 0, 0, W, H, ATLAS_BG, LV_OPA_COVER, 0, ATLAS_BG, 0);
  if (mAtlasRoot == NULL) return -1;
  if (mAtlasWallpaperReady != 0U) {
    lv_obj_t *BgImg = lv_image_create(mAtlasRoot);
    if (BgImg != NULL) {
      lv_obj_set_pos(BgImg, 0, 0);
      lv_obj_set_size(BgImg, W, H);
      lv_image_set_src(BgImg, &mAtlasWallpaperImage);
      lv_image_set_inner_align(BgImg, LV_IMAGE_ALIGN_STRETCH);
      lv_image_set_antialias(BgImg, true);
      lv_obj_remove_flag(BgImg, LV_OBJ_FLAG_CLICKABLE);
    }
  } else {
    lv_obj_set_style_bg_grad_color(mAtlasRoot, lv_color_hex(0x11172a), 0);
    lv_obj_set_style_bg_grad_dir(mAtlasRoot, LV_GRAD_DIR_VER, 0);
  }

  AtlasText(mAtlasRoot, KSHIM_MENU_TEXT_TITLE("KShimC"), mAtlasTitleFont, ATLAS_TEXT,
            Margin, Margin / 2, W * 55 / 100, 72);
  AtlasText(mAtlasRoot, "Xiaomi Pad 6S Pro \u00b7 Android / iPadOS 27 / Linux \u00b7 \u57fa\u4e8e kShimC4sheng \u9879\u76ee\u4e8c\u6539", mAtlasBodyFont,
            0xdce8fa, Margin, Margin / 2 + 76, W * 75 / 100, 40);
  mAtlasLang = AtlasText(mAtlasRoot, kshim_menu_text(KSHIM_TEXT_LANGUAGE_STATE),
                         mAtlasBodyFont, ATLAS_MUTED, W - Margin - 260, Margin / 2 + 34,
                         260, 44);
  if (mAtlasLang != NULL)
    lv_obj_set_style_text_align(mAtlasLang, LV_TEXT_ALIGN_RIGHT, 0);

  mAtlasSidebar = AtlasBox(mAtlasRoot, Margin, HeaderH, SideW, H - HeaderH - FooterH - 20,
                           ATLAS_PANEL, mAtlasWallpaperReady != 0U ? LV_OPA_30 : LV_OPA_70,
                           24, ATLAS_BORDER, 1);
  if (mAtlasSidebar == NULL) return -1;
  if (mAtlasWallpaperReady != 0U) {
    lv_obj_set_style_border_opa(mAtlasSidebar, LV_OPA_30, 0);
  } else {
    lv_obj_set_style_bg_grad_color(mAtlasSidebar, lv_color_hex(0x101726), 0);
    lv_obj_set_style_bg_grad_dir(mAtlasSidebar, LV_GRAD_DIR_VER, 0);
    lv_obj_set_style_border_opa(mAtlasSidebar, LV_OPA_20, 0);
    lv_obj_set_style_shadow_width(mAtlasSidebar, 32, 0);
    lv_obj_set_style_shadow_opa(mAtlasSidebar, LV_OPA_30, 0);
    lv_obj_set_style_shadow_color(mAtlasSidebar, lv_color_hex(0x000000), 0);
  }
  lv_obj_set_style_pad_all(mAtlasSidebar, 16, 0);

  mAtlasContent = AtlasBox(mAtlasRoot, ContentX, HeaderH, ContentW,
                           H - HeaderH - FooterH - 20, ATLAS_PANEL,
                           mAtlasWallpaperReady != 0U ? LV_OPA_30 : LV_OPA_70,
                           24, ATLAS_BORDER, 1);
  if (mAtlasContent == NULL) return -1;
  if (mAtlasWallpaperReady != 0U) {
    lv_obj_set_style_border_opa(mAtlasContent, LV_OPA_30, 0);
  } else {
    lv_obj_set_style_bg_grad_color(mAtlasContent, lv_color_hex(0x121928), 0);
    lv_obj_set_style_bg_grad_dir(mAtlasContent, LV_GRAD_DIR_VER, 0);
    lv_obj_set_style_border_opa(mAtlasContent, LV_OPA_20, 0);
    lv_obj_set_style_shadow_width(mAtlasContent, 36, 0);
    lv_obj_set_style_shadow_opa(mAtlasContent, LV_OPA_30, 0);
    lv_obj_set_style_shadow_color(mAtlasContent, lv_color_hex(0x000000), 0);
  }
  lv_obj_set_style_pad_all(mAtlasContent, 56, 0);

  {
    lv_obj_t *Div = AtlasBox(mAtlasRoot, Margin, H - FooterH, W - 2 * Margin, 1, ATLAS_BORDER,
                             LV_OPA_20, 0, ATLAS_BORDER, 0);
    if (Div != NULL) lv_obj_set_style_border_opa(Div, LV_OPA_20, 0);
  }
  AtlasText(mAtlasRoot, KSHIM_MENU_TEXT_HELP, mAtlasBodyFont, ATLAS_MUTED,
            Margin, H - FooterH + 22, W * 40 / 100, 44);
  mAtlasStatus = AtlasText(mAtlasRoot, "", mAtlasBodyFont, ATLAS_TEXT,
                           W * 42 / 100, H - FooterH + 22, W * 40 / 100, 44);
  if (mAtlasStatus != NULL)
    lv_obj_set_style_text_align(mAtlasStatus, LV_TEXT_ALIGN_CENTER, 0);
  AtlasText(mAtlasRoot, "sheng \u00b7 SM8550", mAtlasBodyFont, ATLAS_MUTED,
            W - Margin - 320, H - FooterH + 22, 320, 44);


  mAtlasBuilt = 1U;
  AtlasRebuild(0U);
  return 0;
}

static void KshimAtlasRefresh(void)
{
  if (!mAtlasBuilt || mContext == NULL) return;
  if (mAtlasLang != NULL)
    lv_label_set_text(mAtlasLang, kshim_menu_text(KSHIM_TEXT_LANGUAGE_STATE));
  AtlasRebuild(mContext->InitialIndex);
}

#endif /* CONFIG_KSHIM_UI_ATLAS */
