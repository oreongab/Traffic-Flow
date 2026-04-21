"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { resetPassword } from "@/lib/api";

export default function ResetPasswordPage() {
  const [identifier, setIdentifier] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSuccess("");

    if (newPassword !== confirm) {
      setError("รหัสผ่านไม่ตรงกัน");
      return;
    }
    if (newPassword.length < 6) {
      setError("รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร");
      return;
    }

    setLoading(true);
    try {
      await resetPassword(identifier, newPassword);
      setSuccess("เปลี่ยนรหัสผ่านสำเร็จ");
      setTimeout(() => router.push("/login"), 1500);
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string } } })?.response?.data
          ?.error || "เกิดข้อผิดพลาด";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-white">
      {/* Top bar */}
      <div className="h-14 bg-[#5ba8e0] flex items-center px-4 shadow-md">
        <Link href="/login" className="flex items-center gap-2">
          <span className="text-white font-bold text-xl">
            Traffix<span className="text-yellow-300">Flow</span>
          </span>
          <span className="text-lg">🚦</span>
        </Link>
      </div>

      {/* Form */}
      <div className="flex items-center justify-center pt-16 px-4">
        <div className="w-full max-w-lg bg-[#b8d8eb] rounded-2xl p-8">
          <h2 className="text-2xl font-bold text-[#4a90d9] text-center mb-6">
            ตั้งรหัสผ่านใหม่
          </h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <p className="text-red-600 text-sm bg-red-100 rounded-lg px-3 py-2">
                {error}
              </p>
            )}
            {success && (
              <p className="text-green-700 text-sm bg-green-100 rounded-lg px-3 py-2">
                {success}
              </p>
            )}

            <input
              type="text"
              required
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              className="w-full bg-white rounded-lg px-4 py-3 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#4a90d9]/30 border border-gray-200"
              placeholder="ชื่อผู้ใช้งาน/อีเมล"
            />

            <input
              type="password"
              required
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="w-full bg-white rounded-lg px-4 py-3 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#4a90d9]/30 border border-gray-200"
              placeholder="รหัสผ่านใหม่"
            />

            <input
              type="password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="w-full bg-white rounded-lg px-4 py-3 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#4a90d9]/30 border border-gray-200"
              placeholder="ยืนยันรหัสผ่าน"
            />

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-[#1e3a5f] hover:bg-[#162e4a] disabled:opacity-50 text-white font-medium rounded-lg py-3 transition-colors text-lg mt-2"
            >
              {loading ? "กำลังดำเนินการ..." : "ยืนยัน"}
            </button>
          </form>

          <p className="text-center text-sm text-gray-600 mt-4">
            <Link href="/login" className="text-[#4a90d9] hover:underline font-medium">
              กลับหน้าเข้าสู่ระบบ
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
