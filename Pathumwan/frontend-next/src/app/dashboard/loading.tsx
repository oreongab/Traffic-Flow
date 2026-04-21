export default function Loading() {
  return (
    <div className="flex items-center justify-center h-screen bg-gray-50">
      <div className="text-center">
        <div className="animate-spin h-10 w-10 border-3 border-[#5ba8e0] border-t-transparent rounded-full mx-auto" />
        <p className="mt-3 text-sm text-gray-400">กำลังโหลดข้อมูล...</p>
      </div>
    </div>
  );
}
