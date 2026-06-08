import React from "react";
import leftPad from "left-pad";
import { z } from "zod";

export function Label({ value }: { value: string }) {
  z.string().parse(value);
  return <span>{leftPad(value, 4)}</span>;
}
